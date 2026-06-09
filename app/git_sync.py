import base64
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from .ai_agent import PROMPTS_DIR, _PROMPT_FILE, VALID_PROMPT_NAMES

logger = logging.getLogger(__name__)

GITHUB_REPO = os.getenv("GITHUB_REPO", "mcdomx/awning_protector")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GIT_TOKEN = os.getenv("GIT_TOKEN", "")


def _git_blob_sha(content: str) -> str:
    data = content.encode("utf-8")
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def _api_headers() -> Dict[str, str]:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GIT_TOKEN:
        headers["Authorization"] = f"token {GIT_TOKEN}"
    return headers


class GitHubPromptSync:
    def __init__(self) -> None:
        self._last_pull_at: Optional[str] = None
        self._last_push_at: Optional[str] = None
        self._cached_remote_shas: Dict[str, str] = {}  # {"prompts/fname": sha}

    def has_local_overrides(self) -> bool:
        """True if any local prompt file differs from the last-known remote SHA."""
        if not self._cached_remote_shas:
            return False
        for name in VALID_PROMPT_NAMES:
            fname = _PROMPT_FILE[name]
            remote_sha = self._cached_remote_shas.get(f"prompts/{fname}")
            if not remote_sha:
                continue
            source = PROMPTS_DIR / fname
            if source.exists() and _git_blob_sha(source.read_text()) != remote_sha:
                return True
        return False

    async def _fetch_tree_shas(self, client: httpx.AsyncClient) -> Dict[str, str]:
        url = (
            f"https://api.github.com/repos/{GITHUB_REPO}"
            f"/git/trees/{GITHUB_BRANCH}?recursive=1"
        )
        r = await client.get(url, headers=_api_headers())
        if r.status_code != 200:
            return {}
        return {
            item["path"]: item["sha"]
            for item in r.json().get("tree", [])
            if item["path"].startswith("prompts/") and item["path"].endswith(".j2")
        }

    async def fetch_remote_shas(self) -> None:
        """Cache remote SHAs at startup so the push button state is correct immediately."""
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                shas = await self._fetch_tree_shas(client)
                if shas:
                    self._cached_remote_shas = shas
        except Exception as exc:
            logger.warning("Failed to fetch remote prompt SHAs: %s", exc)

    async def pull_if_changed(self) -> bool:
        """Pull files from GitHub whose remote SHA differs from local. Returns True if any file updated."""
        updated = False
        async with httpx.AsyncClient(timeout=20) as client:
            remote_shas = await self._fetch_tree_shas(client)
            if not remote_shas:
                return False

            self._cached_remote_shas = remote_shas

            for name in VALID_PROMPT_NAMES:
                fname = _PROMPT_FILE[name]
                remote_sha = remote_shas.get(f"prompts/{fname}")
                if not remote_sha:
                    continue

                source = PROMPTS_DIR / fname
                if source.exists() and _git_blob_sha(source.read_text()) == remote_sha:
                    continue

                url = (
                    f"https://api.github.com/repos/{GITHUB_REPO}"
                    f"/contents/prompts/{fname}"
                )
                r = await client.get(url, headers=_api_headers(),
                                     params={"ref": GITHUB_BRANCH})
                if r.status_code != 200:
                    continue

                raw = base64.b64decode(r.json()["content"]).decode("utf-8")
                source.write_text(raw)
                logger.info("Auto-pulled prompt: %s", fname)
                updated = True

        if updated:
            self._last_pull_at = datetime.now(timezone.utc).isoformat()

        return updated

    async def push_prompts(self) -> Dict[str, str]:
        """Push locally-changed prompt files to GitHub."""
        if not GIT_TOKEN:
            raise ValueError("GIT_TOKEN env var is not set")

        results: Dict[str, str] = {}
        async with httpx.AsyncClient(timeout=20) as client:
            for name in VALID_PROMPT_NAMES:
                fname = _PROMPT_FILE[name]
                source = PROMPTS_DIR / fname
                if not source.exists():
                    continue

                content = source.read_text()
                cached_sha = self._cached_remote_shas.get(f"prompts/{fname}", "")

                if cached_sha and _git_blob_sha(content) == cached_sha:
                    continue  # unchanged

                gh_path = f"prompts/{fname}"
                url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{gh_path}"

                # Fetch current SHA if not cached (GitHub requires it to accept updates)
                remote_sha = cached_sha
                if not remote_sha:
                    r = await client.get(url, headers=_api_headers(),
                                         params={"ref": GITHUB_BRANCH})
                    remote_sha = r.json().get("sha", "") if r.status_code == 200 else ""

                encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
                payload: Dict[str, Any] = {
                    "message": f"Update {fname} via awning-protector UI",
                    "content": encoded,
                    "branch": GITHUB_BRANCH,
                }
                if remote_sha:
                    payload["sha"] = remote_sha

                r = await client.put(url, headers=_api_headers(), json=payload)
                if r.status_code in (200, 201):
                    new_sha = r.json().get("content", {}).get("sha", "")
                    if new_sha:
                        self._cached_remote_shas[f"prompts/{fname}"] = new_sha
                    results[name] = "pushed"
                    logger.info("Pushed prompt %s to GitHub", fname)
                else:
                    results[name] = f"error:{r.status_code}"
                    logger.warning("Failed to push %s: %s", fname, r.status_code)

        if any(v == "pushed" for v in results.values()):
            self._last_push_at = datetime.now(timezone.utc).isoformat()

        return results

    def status(self) -> Dict[str, Any]:
        return {
            "has_local_overrides": self.has_local_overrides(),
            "last_pull_at": self._last_pull_at,
            "last_push_at": self._last_push_at,
            "github_token_set": bool(GIT_TOKEN),
        }


git_sync = GitHubPromptSync()
