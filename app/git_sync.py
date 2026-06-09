import base64
import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from .ai_agent import DATA_PROMPTS_DIR, PROMPTS_DIR, _PROMPT_FILE, VALID_PROMPT_NAMES

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

    def has_local_overrides(self) -> bool:
        """True if data/prompts/ has any file that differs from prompts/."""
        for name in VALID_PROMPT_NAMES:
            override = DATA_PROMPTS_DIR / _PROMPT_FILE[name]
            source = PROMPTS_DIR / _PROMPT_FILE[name]
            if override.exists():
                if not source.exists() or override.read_text() != source.read_text():
                    return True
        return False

    async def push_prompts(self) -> Dict[str, str]:
        """Copy data/prompts/ overrides to prompts/ and push each to GitHub, then remove overrides."""
        if not GIT_TOKEN:
            raise ValueError("GIT_TOKEN env var is not set")

        results: Dict[str, str] = {}
        async with httpx.AsyncClient(timeout=20) as client:
            for name in VALID_PROMPT_NAMES:
                fname = _PROMPT_FILE[name]
                override = DATA_PROMPTS_DIR / fname
                source = PROMPTS_DIR / fname
                if not override.exists():
                    continue

                content = override.read_text()
                source.write_text(content)

                gh_path = f"prompts/{fname}"
                url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{gh_path}"

                # Fetch current SHA so GitHub accepts the update
                r = await client.get(url, headers=_api_headers(),
                                     params={"ref": GITHUB_BRANCH})
                sha = r.json().get("sha", "") if r.status_code == 200 else ""

                encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
                payload: Dict[str, Any] = {
                    "message": f"Update {fname} via awning-protector UI",
                    "content": encoded,
                    "branch": GITHUB_BRANCH,
                }
                if sha:
                    payload["sha"] = sha

                r = await client.put(url, headers=_api_headers(), json=payload)
                if r.status_code in (200, 201):
                    override.unlink()
                    results[name] = "pushed"
                    logger.info("Pushed prompt %s to GitHub", fname)
                else:
                    results[name] = f"error:{r.status_code}"
                    logger.warning("Failed to push %s: %s", fname, r.status_code)

        if any(v == "pushed" for v in results.values()):
            self._last_push_at = datetime.now(timezone.utc).isoformat()

        return results

    async def _remote_prompt_shas(self, client: httpx.AsyncClient) -> Dict[str, str]:
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

    async def pull_if_changed(self) -> bool:
        """Pull prompt files from GitHub when the remote SHA differs from local. Returns True if any file updated."""
        updated = False
        async with httpx.AsyncClient(timeout=20) as client:
            remote_shas = await self._remote_prompt_shas(client)
            if not remote_shas:
                return False

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

    def status(self) -> Dict[str, Any]:
        return {
            "has_local_overrides": self.has_local_overrides(),
            "last_pull_at": self._last_pull_at,
            "last_push_at": self._last_push_at,
            "github_token_set": bool(GIT_TOKEN),
        }


git_sync = GitHubPromptSync()
