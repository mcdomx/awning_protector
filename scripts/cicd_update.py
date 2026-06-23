#!/usr/bin/env python3
"""CI/CD polling script — generic, config-driven via .env. See README-PI.md."""

import argparse
import logging
import os
import shutil
import sys
import subprocess
import time
from pathlib import Path

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


def install_dependencies() -> None:
    pipenv_bin = shutil.which("pipenv")
    if not pipenv_bin:
        raise RuntimeError("pipenv not found on PATH")
    env = {**os.environ, "PIPENV_VENV_IN_PROJECT": "1"}
    out = _run([pipenv_bin, "install"], cwd=PROJECT_ROOT, env=env)
    logger.info("pipenv install: %s", out or "ok")


def deploy_systemd() -> None:
    service_name = load_env_var("CICD_SERVICE_NAME", None)
    if not service_name:
        raise RuntimeError("CICD_SERVICE_NAME must be set in .env when CICD_DEPLOY_MODE=systemd")
    install_dependencies()
    _run([SYSTEMCTL, "restart", service_name])
    logger.info("Service '%s' restarted.", service_name)


def deploy_docker() -> None:
    compose_file = load_env_var("CICD_COMPOSE_FILE", "docker-compose.yml")
    docker_bin = shutil.which("docker")
    if not docker_bin:
        raise RuntimeError("docker not found on PATH")
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
