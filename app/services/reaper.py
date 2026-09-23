from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.core.config import Settings
from app.core.database import Database
from app.services.docker import ContainerError, DockerService

logger = logging.getLogger("syclover.reaper")


def expire_instances(
    database: Database, docker: DockerService, now: datetime | None = None
) -> list[str]:
    """Stop every instance whose TTL elapsed, for all users, and return their ids."""
    moment = now or datetime.now(UTC)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, container_id, expires_at FROM instances "
            "WHERE status IN ('starting', 'running')"
        ).fetchall()
    expired: list[str] = []
    for row in rows:
        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
        except ValueError:
            expires_at = moment
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at > moment:
            continue
        with database.connect() as connection:
            claimed = connection.execute(
                "UPDATE instances SET status = 'stopped', stopped_at = ? "
                "WHERE id = ? AND status IN ('starting', 'running') AND expires_at = ?",
                (moment.isoformat(), row["id"], row["expires_at"]),
            )
        if claimed.rowcount != 1:
            # A concurrent extension or manual stop won the race.
            continue
        if row["container_id"]:
            try:
                docker.stop(row["container_id"])
            except ContainerError as exc:
                logger.warning("Instance %s could not be stopped: %s", row["id"], exc)
                with database.connect() as connection:
                    connection.execute(
                        "UPDATE instances SET error_message = ? WHERE id = ?",
                        (str(exc), row["id"]),
                    )
        expired.append(row["id"])
    return expired


def prune_orphan_containers(database: Database, docker: DockerService) -> list[str]:
    """Stop labelled challenge containers that no live instance row references."""
    try:
        containers = docker.list_managed_containers()
    except ContainerError as exc:
        logger.warning("Orphan container scan failed: %s", exc)
        return []
    if not containers:
        return []
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT container_name FROM instances WHERE status IN ('starting', 'running')"
        ).fetchall()
    known = {row["container_name"] for row in rows}
    removed: list[str] = []
    for container in containers:
        if container["name"] in known:
            continue
        try:
            docker.stop(container["id"])
        except ContainerError as exc:
            logger.warning("Orphan container %s could not be stopped: %s", container["name"], exc)
            continue
        logger.info("Stopped orphan challenge container %s", container["name"])
        removed.append(container["name"])
    return removed


def sweep(database: Database, docker: DockerService, now: datetime | None = None) -> dict[str, list[str]]:
    """One reclamation pass: expire instances, then drop untracked containers."""
    return {
        "expired": expire_instances(database, docker, now),
        "orphans": prune_orphan_containers(database, docker),
    }


async def run_reaper(database: Database, docker: DockerService, settings: Settings) -> None:
    """Background loop that keeps container usage aligned with the instance table."""
    interval = settings.instance_reaper_interval_seconds
    while True:
        try:
            result = await asyncio.to_thread(sweep, database, docker)
            if result["expired"] or result["orphans"]:
                logger.info(
                    "Reaper expired %d instance(s) and stopped %d orphan container(s)",
                    len(result["expired"]),
                    len(result["orphans"]),
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # keep the loop alive; a failed sweep must not kill the reaper
            logger.exception("Instance reaper sweep failed")
        await asyncio.sleep(interval)
