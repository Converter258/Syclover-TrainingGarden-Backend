from __future__ import annotations

import re
import stat
import subprocess
import tempfile
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


def script_interpreter(path: Path) -> list[str]:
    """Pick a syntax checker from the shebang so Python checks validate like shell ones."""
    try:
        first_line = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        first_line = ""
    if "python" in first_line:
        return ["python3", "-m", "py_compile"]
    return ["/bin/sh", "-n"]


def _patch_entries(archive: Path, *, max_files: int = 2_000, max_uncompressed_bytes: int = 256 * 1024 * 1024):
    try:
        bundle = ZipFile(archive)
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise ValueError("Patch file must be a valid ZIP archive") from exc
    try:
        entries = bundle.infolist()
        if not entries or len(entries) > max_files:
            raise ValueError("Patch archive is empty or contains too many files")
        total_size = 0
        files = []
        seen_paths: set[str] = set()
        for entry in entries:
            if not entry.filename or "\\" in entry.filename:
                raise ValueError("Patch archive contains an unsafe path")
            path = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (
                path == PurePosixPath(".")
                or path.is_absolute()
                or ".." in path.parts
                or (path.parts and re.match(r"^[A-Za-z]:", path.parts[0]))
            ):
                raise ValueError("Patch archive contains an unsafe path")
            if stat.S_ISLNK(mode):
                raise ValueError("Patch archive cannot contain symbolic links")
            normalized_name = path.as_posix()
            if normalized_name in seen_paths:
                raise ValueError("Patch archive contains duplicate paths")
            seen_paths.add(normalized_name)
            total_size += entry.file_size
            if total_size > max_uncompressed_bytes:
                raise ValueError("Patch archive expands beyond the allowed size")
            if not entry.is_dir():
                files.append(normalized_name)
        if "fix.sh" not in files:
            raise ValueError("Patch archive must contain a root-level fix.sh")
        if any(name == "" for name in files):
            raise ValueError("Patch archive contains an invalid file name")
        return bundle, entries, files
    except Exception:
        bundle.close()
        raise


def extract_patch_archive(
    archive: Path,
    destination: Path,
    *,
    category: str | None = None,
    max_files: int = 2_000,
    max_uncompressed_bytes: int = 256 * 1024 * 1024,
) -> list[str]:
    """Validate and safely extract an AWDP patch bundle."""
    bundle, entries, files = _patch_entries(
        archive, max_files=max_files, max_uncompressed_bytes=max_uncompressed_bytes
    )
    try:
        extras = [name for name in files if name != "fix.sh"]
        if category == "Pwn" and len(extras) != 1:
            raise ValueError("Pwn patch archive must contain fix.sh and exactly one binary file")
        if category == "Web" and not extras:
            raise ValueError("Web patch archive must contain fix.sh and the service files")
        destination.mkdir(parents=True, exist_ok=True)
        bundle.extractall(destination)
    finally:
        bundle.close()
    fix_path = destination / "fix.sh"
    valid, output = validate_asset(fix_path, "fix_script")
    if not valid:
        raise ValueError(f"fix.sh failed syntax validation: {output}")
    return files


def validate_patch_archive(path: Path, category: str | None = None) -> tuple[bool, str]:
    try:
        with tempfile.TemporaryDirectory(prefix="syclover-patch-") as directory:
            files = extract_patch_archive(path, Path(directory), category=category)
    except (ValueError, OSError) as exc:
        return False, str(exc)
    kind = f"{category} " if category else ""
    return True, f"{kind}patch.zip is valid; contains: {', '.join(files)}"


def validate_asset(path: Path, kind: str, *, category: str | None = None) -> tuple[bool, str]:
    if kind in {"check_script", "fix_script"}:
        command = script_interpreter(path)
        try:
            result = subprocess.run(
                [*command, str(path)],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return False, "Syntax validation timed out."
        except FileNotFoundError:
            return False, f"{command[0]} is not available to validate this script."
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            return True, f"Syntax is valid ({command[0]})."
        return False, output or "Script failed syntax validation."
    if kind == "patch":
        return validate_patch_archive(path, category)
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
