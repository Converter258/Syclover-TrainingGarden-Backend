#!/usr/bin/env python3
"""Publish GitHub source snapshots to a VPS-local Git delivery repository."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path("/srv/training-garden")
WORK = ROOT / "delivery-work"
BARE = ROOT / "delivery.git"
REPOS = ("Syclover-TrainingGarden-Backend", "Syclover-TrainingGarden-Frontend")
BASE = {
    REPOS[0]: "703cb42c47fc88dd503dc72f201c3bd36c1c98eb",
    REPOS[1]: "40b8745c54d6d5afa5d26a6f48549321a0489c7f",
}
HEADERS = {"User-Agent": "training-garden-source-sync", "Accept": "application/vnd.github+json"}


def run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


def fetch(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=35) as response:
                payload = response.read(30_000_001)
            if len(payload) > 30_000_000:
                raise ValueError("source archive exceeds 30 MB")
            return payload
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(5)
    raise RuntimeError(f"cannot download {url}: {last_error}")


def branch_sha(owner: str, repo: str, branch: str) -> str:
    path = urllib.parse.quote(branch, safe="")
    response = json.loads(fetch(f"https://api.github.com/repos/{owner}/{repo}/commits/{path}"))
    sha = response["sha"]
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise ValueError("invalid GitHub commit SHA")
    return sha


def archive(owner: str, repo: str, sha: str, target: Path) -> None:
    data = fetch(f"https://codeload.github.com/{owner}/{repo}/tar.gz/{sha}")
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
        temporary.write(data)
        pending = Path(temporary.name)
    pending.replace(target)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    if not BARE.exists():
        run("git", "init", "--bare", str(BARE))
    if not WORK.exists():
        WORK.mkdir()
        run("git", "init", "-b", "main", cwd=WORK)
        run("git", "remote", "add", "delivery", str(BARE), cwd=WORK)
        run("git", "config", "user.name", "Training Garden source mirror", cwd=WORK)
        run("git", "config", "user.email", "source@localhost", cwd=WORK)

    manifest: dict[str, dict[str, str]] = {}
    for repo in REPOS:
        versions = {
            "base": BASE[repo],
            "upstream-main": branch_sha("K4per", repo, "main"),
            "deploy-vm101": branch_sha("Converter258", repo, "deploy/vm101"),
        }
        manifest[repo] = versions
        folder = WORK / repo
        folder.mkdir(exist_ok=True)
        for key, sha in versions.items():
            target = folder / f"{key}.tar.gz"
            stamp = folder / f"{key}.sha"
            if target.is_file() and stamp.is_file() and stamp.read_text().strip() == sha:
                continue
            owner = "K4per" if key in {"base", "upstream-main"} else "Converter258"
            archive(owner, repo, sha, target)
            stamp.write_text(sha + "\n")
    (WORK / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    run("git", "add", "-A", cwd=WORK)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=WORK).returncode:
        run("git", "commit", "-m", "Refresh Training Garden source snapshots", cwd=WORK)
        run("git", "push", "delivery", "main", cwd=WORK)
        run("git", "-C", str(BARE), "symbolic-ref", "HEAD", "refs/heads/main")
    print("Training Garden source delivery updated:", json.dumps(manifest))


if __name__ == "__main__":
    main()
