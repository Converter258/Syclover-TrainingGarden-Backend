from __future__ import annotations

import re
import stat
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile


def safe_filename(filename: str) -> str:
    name = Path(filename).name[:120]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return cleaned or "upload.bin"


def store_bytes(storage_path: Path, challenge_id: str, filename: str, content: bytes) -> tuple[str, Path]:
    directory = storage_path / challenge_id
    directory.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}-{safe_filename(filename)}"
    target = directory / stored_name
    target.write_bytes(content)
    return f"{challenge_id}/{stored_name}", target


def validate_asset(path: Path, kind: str) -> tuple[bool, str]:
    if kind in {"check_script", "fix_script"}:
        try:
            result = subprocess.run(
                ["/bin/sh", "-n", str(path)],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            return False, "Shell syntax validation timed out."
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output or "Shell syntax is valid."
    if kind == "patch":
        text = path.read_text(encoding="utf-8", errors="replace")
        valid = ("diff --git " in text) or ("--- " in text and "+++ " in text and "@@" in text)
        return valid, "Unified diff structure detected." if valid else "Expected a unified diff patch."
    return True, "Attachment is available."


def extract_build_archive(
    archive: Path,
    destination: Path,
    *,
    max_files: int = 2_000,
    max_uncompressed_bytes: int = 256 * 1024 * 1024,
) -> Path:
    """Safely extract a ZIP and return the directory containing its only Dockerfile."""
    try:
        with ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if not entries or len(entries) > max_files:
                raise ValueError("Build archive is empty or contains too many files")
            total_size = 0
            for entry in entries:
                if "\\" in entry.filename:
                    raise ValueError("Build archive contains an unsafe path")
                path = PurePosixPath(entry.filename)
                mode = entry.external_attr >> 16
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or (path.parts and path.parts[0].endswith(":"))
                ):
                    raise ValueError("Build archive contains an unsafe path")
                if stat.S_ISLNK(mode):
                    raise ValueError("Build archive cannot contain symbolic links")
                total_size += entry.file_size
                if total_size > max_uncompressed_bytes:
                    raise ValueError("Build archive expands beyond the allowed size")
            bundle.extractall(destination)
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise ValueError("Build file must be a valid ZIP archive") from exc

    dockerfiles = [path for path in destination.rglob("Dockerfile") if path.is_file()]
    if len(dockerfiles) != 1:
        raise ValueError("Build archive must contain exactly one Dockerfile")
    return dockerfiles[0].parent


def detect_exposed_port(context: Path) -> int | None:
    dockerfile = (context / "Dockerfile").read_text(encoding="utf-8", errors="replace")
    match = re.search(r"(?im)^\s*EXPOSE\s+(\d{1,5})(?:/tcp)?(?:\s|$)", dockerfile)
    if not match:
        return None
    port = int(match.group(1))
    return port if 1 <= port <= 65535 else None
