#!/usr/bin/env python3
"""CI/CD polling script — generic, config-driven via .env. See README-PI.md."""

import argparse
import base64
import json
import logging
import os
import shutil
import socket
import struct
import sys
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

# ── Constants ──────────────────────────────────────────────────────────────
PROJECT_ROOT  = Path(__file__).resolve().parent.parent
LOG_DIR       = PROJECT_ROOT / "logs"
LOG_FILE      = LOG_DIR / "cicd.log"
FLAG_FILE     = PROJECT_ROOT / ".cicd_disabled"
LAST_RUN_FILE = LOG_DIR / ".last_run"
GIT           = "/usr/bin/git"
SYSTEMCTL     = "/usr/bin/systemctl"
DEFAULT_INTERVAL = 15

logger = logging.getLogger(__name__)


# ── Logging ────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ── .env config ────────────────────────────────────────────────────────────

def load_env_var(key: str, default):
    """Read KEY from .env file, falling back to the real environment, then default."""
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return os.environ.get(key, default)


def load_interval() -> int:
    try:
        return int(load_env_var("CICD_INTERVAL_MINUTES", DEFAULT_INTERVAL))
    except ValueError:
        return DEFAULT_INTERVAL


# ── Interval gating ────────────────────────────────────────────────────────

def is_too_soon(interval_minutes: int) -> bool:
    """Return True if fewer than interval_minutes have elapsed since last run."""
    if not LAST_RUN_FILE.exists():
        return False
    try:
        last = float(LAST_RUN_FILE.read_text().strip())
        elapsed = (time.time() - last) / 60
        return elapsed < interval_minutes
    except (ValueError, OSError):
        return False


def record_run_time() -> None:
    LAST_RUN_FILE.write_text(str(time.time()))


# ── Git / deploy helpers ───────────────────────────────────────────────────

def _run(cmd: list, **kwargs) -> str:
    """Run a subprocess command; raise RuntimeError on non-zero exit."""
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(str(c) for c in cmd)!r} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def fetch_remote(branch: str) -> None:
    _run([GIT, "fetch", "origin", branch], cwd=PROJECT_ROOT)


def get_local_commit() -> str:
    return _run([GIT, "rev-parse", "HEAD"], cwd=PROJECT_ROOT)


def get_remote_commit(branch: str) -> str:
    return _run([GIT, "rev-parse", f"origin/{branch}"], cwd=PROJECT_ROOT)


def git_pull(branch: str) -> None:
    out = _run([GIT, "pull", "origin", branch], cwd=PROJECT_ROOT)
    logger.info("git pull: %s", out)


_SEARCH_PATH = ":".join([
    os.environ.get("PATH", ""),
    "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin",
    "/home/mcdomx/.local/bin",
])


def _find_bin(name: str) -> str:
    """Locate an executable, extending PATH with common install dirs missed by cron."""
    found = shutil.which(name, path=_SEARCH_PATH)
    if not found:
        raise RuntimeError(f"{name} not found on PATH")
    return found


def install_dependencies() -> None:
    pipenv_bin = _find_bin("pipenv")
    env = {**os.environ, "PIPENV_VENV_IN_PROJECT": "1"}
    out = _run([pipenv_bin, "install"], cwd=PROJECT_ROOT, env=env)
    logger.info("pipenv install: %s", out or "ok")


def deploy_systemd() -> None:
    service_name = load_env_var("CICD_SERVICE_NAME", None)
    if not service_name:
        raise RuntimeError("CICD_SERVICE_NAME must be set in .env when CICD_DEPLOY_MODE=systemd")
    install_dependencies()
    _run(["sudo", SYSTEMCTL, "restart", service_name])
    logger.info("Service '%s' restarted.", service_name)
    reload_kiosk_if_configured()


# ── Kiosk reload (Chrome DevTools Protocol) ───────────────────────────────
# systemctl restart reloads the Python backend, but a kiosk Chromium tab
# that's already open never re-navigates on its own — static-file-only
# changes (CSS/JS) would otherwise sit stale until the next reboot. If
# CICD_KIOSK_URL is set, force that tab to hard-reload (bypassing its disk
# cache) via the CDP debug port opened by deploy/kiosk-autostart.

def reload_kiosk_if_configured() -> None:
    kiosk_url = load_env_var("CICD_KIOSK_URL", None)
    if not kiosk_url:
        return
    debug_port = load_env_var("CICD_KIOSK_DEBUG_PORT", "9222")
    try:
        with urlopen(f"http://127.0.0.1:{debug_port}/json/list", timeout=5) as resp:
            tabs = json.loads(resp.read())
        tab = next((t for t in tabs if t.get("url", "").startswith(kiosk_url)), None)
        if tab is None:
            raise RuntimeError(f"no Chromium tab found for {kiosk_url}")
        _cdp_reload(tab["webSocketDebuggerUrl"])
        logger.info("Kiosk tab reloaded (%s).", kiosk_url)
    except Exception as exc:
        logger.warning("Kiosk reload skipped: %s", exc)


def _cdp_reload(ws_url: str) -> None:
    """Minimal WebSocket client: send one Page.reload command over the CDP socket."""
    parsed = urlparse(ws_url)
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    key = base64.b64encode(os.urandom(16)).decode()

    with socket.create_connection((parsed.hostname, parsed.port), timeout=5) as sock:
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(handshake.encode())
        response = sock.recv(4096)
        if b"101" not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"WebSocket handshake failed: {response[:80]!r}")

        payload = json.dumps(
            {"id": 1, "method": "Page.reload", "params": {"ignoreCache": True}}
        ).encode()
        sock.sendall(_ws_frame(payload))


def _ws_frame(payload: bytes) -> bytes:
    """Encode a single masked text frame (client->server frames must be masked per RFC 6455)."""
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    header = bytearray([0x81])  # FIN + text frame opcode
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    header += mask
    return bytes(header) + masked


def deploy_docker() -> None:
    compose_file = load_env_var("CICD_COMPOSE_FILE", "docker-compose.yml")
    docker_bin = _find_bin("docker")
    # The image is built locally from the Dockerfile (no registry to `pull`
    # from), so the container only picks up new commits if rebuilt here.
    out = _run([docker_bin, "compose", "-f", compose_file, "up", "-d", "--build"], cwd=PROJECT_ROOT)
    logger.info("docker compose up -d --build: %s", out or "ok")


def deploy() -> None:
    mode = load_env_var("CICD_DEPLOY_MODE", "systemd")
    if mode == "docker":
        deploy_docker()
    elif mode == "systemd":
        deploy_systemd()
    else:
        raise RuntimeError(f"Unknown CICD_DEPLOY_MODE={mode!r} (expected 'systemd' or 'docker')")


# ── Entry point ────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ignore-interval",
        action="store_true",
        help="Skip the CICD_INTERVAL_MINUTES throttle (used by the @reboot cron entry)",
    )
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()

    env = os.environ.get("ENVIRONMENT", "development")
    if env != "production":
        logger.info("ENVIRONMENT=%s — CI/CD automation disabled. Exiting.", env)
        sys.exit(0)

    if FLAG_FILE.exists():
        logger.info(".cicd_disabled flag present — automation paused. Exiting.")
        sys.exit(0)

    interval = load_interval()
    if not args.ignore_interval and is_too_soon(interval):
        sys.exit(0)  # silent — most cron fires hit this path
    record_run_time()
    logger.info("--- CI/CD poll starting (interval: %d min) ---", interval)

    branch = load_env_var("CICD_GIT_BRANCH", "main")

    try:
        fetch_remote(branch)
        local  = get_local_commit()
        remote = get_remote_commit(branch)

        if local == remote:
            logger.info("No new commits (HEAD=%s). Nothing to do.", local[:8])
            sys.exit(0)

        logger.info("New commits: local=%s → remote=%s", local[:8], remote[:8])
        git_pull(branch)
        deploy()
        logger.info("Deployment complete.")

    except RuntimeError as exc:
        logger.error("Deployment aborted: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
