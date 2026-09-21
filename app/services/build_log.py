"""Live log buffer for challenge image builds.

A build runs inside one HTTP request, but the browser also opens a second connection
to watch progress. The registry keeps the growing log in memory so the admin console
can poll it while the build is still running, then falls back to the persisted
``build_output`` once the request finishes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock

LOG_LIMIT = 400_000
POLL_SECONDS = 0.5
IDLE_LIMIT_SECONDS = 120.0


@dataclass
class BuildRecord:
    challenge_id: str
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    status: str = "building"
    lines: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def finished(self) -> bool:
        return self.finished_at is not None

    @property
    def log(self) -> str:
        return "".join(self.lines)


class BuildRegistry:
    """Thread-safe registry of the most recent build per challenge."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._builds: dict[str, BuildRecord] = {}

    def start(self, challenge_id: str) -> BuildRecord:
        with self._lock:
            record = BuildRecord(challenge_id=challenge_id)
            self._builds[challenge_id] = record
            return record

    def append(self, challenge_id: str, text: str) -> None:
        if not text:
            return
        with self._lock:
            record = self._builds.get(challenge_id)
            if record is None or record.finished:
                return
            record.lines.append(text)
            if sum(len(line) for line in record.lines) > LOG_LIMIT:
                # Keep the tail, which is where build failures explain themselves.
                record.lines = record.lines[len(record.lines) // 2 :]
                record.truncated = True

    def finish(self, challenge_id: str, status: str, output: str | None = None) -> None:
        with self._lock:
            record = self._builds.get(challenge_id)
            if record is None:
                return
            if output:
                record.lines = [output if output.endswith("\n") else f"{output}\n"]
                record.truncated = False
            record.status = status
            record.finished_at = datetime.now(UTC).isoformat()

    def snapshot(self, challenge_id: str) -> BuildRecord | None:
        with self._lock:
            record = self._builds.get(challenge_id)
            if record is None:
                return None
            return BuildRecord(
                challenge_id=record.challenge_id,
                started_at=record.started_at,
                finished_at=record.finished_at,
                status=record.status,
                lines=list(record.lines),
                truncated=record.truncated,
            )


registry = BuildRegistry()
