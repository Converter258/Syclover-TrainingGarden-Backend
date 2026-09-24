#!/usr/bin/env python3
"""Unattended VM101 release updater. Run as root through the systemd timer."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen


BASE = Path("/opt/training-garden")
ETC = Path("/etc/training-garden")
DATA = Path("/var/lib/training-garden/updater")
ENV_FILE = ETC / "platform.env"
SOURCE = "tg-source@47.109.46.12:/srv/training-garden/mirror"
REPOS = ("Syclover-TrainingGarden-Backend", "Syclover-TrainingGarden-Frontend")
SSH = "ssh -i /etc/training-garden/source_key -o UserKnownHostsFile=/etc/training-garden/source_known_hosts -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10"


def run(*args: str, cwd: Path | None = None, capture: bool = False, timeout: int = 900) -> str:
    result = subprocess.run(
        args, cwd=cwd, env={**os.environ, "GIT_SSH_COMMAND": SSH, "COMPOSE_PROGRESS": "plain"},
        text=True, stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None, timeout=timeout, check=True,
    )
    return result.stdout.strip() if capture else ""


def state(status: str, **details: str) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    payload = {"status": status, "time_utc": datetime.now(timezone.utc).isoformat(), **details}
    (DATA / "status.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def compose(release: Path, *args: str, timeout: int = 900) -> None:
    run("docker", "compose", "-p", "training-garden", "--env-file", str(ENV_FILE),
        "-f", str(release / "compose.yaml"), "-f", str(release / "compose.vm101.yaml"),
        *args, timeout=timeout)


def image_id(name: str) -> str:
    return run("docker", "inspect", name, "--format", "{{.Image}}", capture=True)


def schema(path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(path) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"database integrity check failed: {integrity}")
        return db.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('table','index','trigger','view') AND name NOT LIKE 'sqlite_autoindex_%' "
            "ORDER BY type, name"
        ).fetchall()


def update() -> None:
    current = (BASE / "current").resolve(strict=True)
    commits: dict[str, str] = {}
    for repo in REPOS:
        mirror = DATA / f"{repo}.git"
        remote = f"{SOURCE}/{repo}.git"
        if not mirror.exists():
            run("git", "clone", "--mirror", remote, str(mirror), timeout=120)
        else:
            run("git", "-C", str(mirror), "remote", "update", "--prune", timeout=120)
        for branch in ("upstream-main", "deploy-vm101"):
            commits[f"{repo}/{branch}"] = run(
                "git", "-C", str(mirror), "rev-parse", f"refs/heads/{branch}", capture=True
            )

    current_manifest = (current / "RELEASE_MANIFEST.txt")
    if current_manifest.exists():
        manifest = current_manifest.read_text()
        if all(f"{repo}: {sha}" in manifest for repo, sha in commits.items()):
            state("up_to_date", release=str(current), **commits)
            return
    status_file = DATA / "status.json"
    if status_file.exists():
        previous_status = json.loads(status_file.read_text())
        if previous_status.get("status") == "review_required" and all(
            previous_status.get(repo) == sha for repo, sha in commits.items()
        ):
            print("Previously staged schema-changing release still needs review", flush=True)
            return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    release = BASE / "releases" / (
        f"{stamp}-{commits[f'{REPOS[0]}/upstream-main'][:7]}"
        f"-{commits[f'{REPOS[1]}/upstream-main'][:7]}"
    )
    release.mkdir(parents=True, exist_ok=False)
    for repo in REPOS:
        dest = release / repo
        run("git", "clone", "--quiet", "--branch", "deploy-vm101", str(DATA / f"{repo}.git"), str(dest))
        run("git", "config", "user.name", "Training Garden deployment", cwd=dest)
        run("git", "config", "user.email", "deploy@localhost", cwd=dest)
        try:
            run("git", "merge", "--no-ff", "--no-edit", "origin/upstream-main", cwd=dest, timeout=120)
        except subprocess.CalledProcessError:
            state("review_required", release=str(release), reason=f"merge conflict in {repo}", **commits)
            return
    deployment = release / REPOS[0] / "deployment" / "vm101"
    for filename in ("compose.yaml", "compose.vm101.yaml", "Caddyfile"):
        shutil.copy2(deployment / filename, release / filename)
    (release / "RELEASE_MANIFEST.txt").write_text(
        "Training Garden VM101 automated release\n"
        + "".join(f"{repo}: {sha}\n" for repo, sha in commits.items())
        + "Production sources: Converter258 fork deploy/vm101 merged with K4per main\n"
    )
    compose(release, "config", "-q")

    old_tags = {}
    for part in ("backend", "frontend"):
        old_tags[part] = f"training-garden-{part}:before-{stamp}"
        run("docker", "tag", image_id(f"training-garden-{part}-1"), old_tags[part])
    compose(release, "build", "backend", "frontend", timeout=1800)
    run("docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
        "training-garden-backend:latest", "-m", "compileall", "-q", "/app/app")

    run("/usr/local/sbin/training-garden-backup", timeout=300)
    backup = max((Path("/var/backups/training-garden/database")).glob("*.db"),
                 key=lambda item: item.stat().st_mtime)
    with tempfile.TemporaryDirectory(prefix="tg-migration-") as temporary:
        db_copy = Path(temporary) / "training_garden.db"
        shutil.copy2(backup, db_copy)
        before = schema(db_copy)
        run("docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
            "-v", f"{temporary}:/tmp/tg", "training-garden-backend:latest", "-c",
            'from pathlib import Path; from app.core.database import Database; Database(Path("/tmp/tg/training_garden.db")).initialize()')
        after = schema(db_copy)
        if before != after:
            state("review_required", release=str(release), reason="database schema changes", backup=str(backup), **commits)
            return

    pending = BASE / ".current-updater-pending"
    pending.unlink(missing_ok=True)
    pending.symlink_to(release)
    pending.replace(BASE / "current")
    try:
        compose(release, "up", "-d", "--no-deps", "--force-recreate", "backend", "frontend", timeout=180)
        success = 0
        for _ in range(30):
            try:
                with urlopen("http://192.168.10.24:8080/health", timeout=5) as response:
                    healthy = response.status == 200 and b'"ok"' in response.read()
                healthy &= all(
                    image_id(f"training-garden-{part}-1") ==
                    run("docker", "image", "inspect", f"training-garden-{part}:latest", "--format", "{{.Id}}", capture=True)
                    for part in ("backend", "frontend")
                )
                success = success + 1 if healthy else 0
                if success >= 2:
                    state("deployed", release=str(release), previous=str(current), backup=str(backup), **commits)
                    return
            except Exception:
                success = 0
            time.sleep(3)
        raise RuntimeError("new release health check failed")
    except Exception:
        for part in ("backend", "frontend"):
            run("docker", "tag", old_tags[part], f"training-garden-{part}:latest")
        rollback = BASE / ".current-updater-rollback"
        rollback.unlink(missing_ok=True)
        rollback.symlink_to(current)
        rollback.replace(BASE / "current")
        compose(current, "up", "-d", "--no-deps", "--force-recreate", "backend", "frontend", timeout=180)
        state("rolled_back", attempted=str(release), restored=str(current), backup=str(backup))
        raise


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "update.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            update()
            return 0
        except Exception as exc:
            state("failed", error=str(exc))
            raise


if __name__ == "__main__":
    sys.exit(main())
