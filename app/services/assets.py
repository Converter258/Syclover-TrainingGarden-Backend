from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path


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
