from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DatabaseDep, DockerDep, SettingsDep
from app.schemas import InstancePublic, Message
from app.services.docker import ContainerError

router = APIRouter(prefix="/instances", tags=["instances"])


def _serialize(row) -> InstancePublic:
    return InstancePublic.model_validate(dict(row))


def _expire_instances(user: dict, database, docker) -> None:
    now = datetime.now(UTC)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, container_id, expires_at FROM instances "
            "WHERE user_id = ? AND status IN ('starting', 'running')",
            (user["id"],),
        ).fetchall()
    for row in rows:
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at > now:
            continue
        error = None
        if row["container_id"]:
            try:
                docker.stop(row["container_id"])
            except ContainerError as exc:
                error = str(exc)
        with database.connect() as connection:
            connection.execute(
                "UPDATE instances SET status = 'stopped', stopped_at = ?, error_message = COALESCE(?, error_message) "
                "WHERE id = ? AND status IN ('starting', 'running')",
                (now.isoformat(), error, row["id"]),
            )


@router.get("", response_model=list[InstancePublic])
async def list_instances(user: CurrentUser, database: DatabaseDep, docker: DockerDep) -> list[InstancePublic]:
    _expire_instances(user, database, docker)
    with database.connect() as connection:
        if user["role"] == "admin":
            rows = connection.execute(
                "SELECT i.*, c.title AS challenge_title FROM instances i "
                "JOIN challenges c ON c.id = i.challenge_id ORDER BY i.created_at DESC"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT i.*, c.title AS challenge_title FROM instances i "
                "JOIN challenges c ON c.id = i.challenge_id WHERE i.user_id = ? "
                "ORDER BY i.created_at DESC",
                (user["id"],),
            ).fetchall()
    return [_serialize(row) for row in rows]


@router.post("/{challenge_id}", response_model=InstancePublic, status_code=status.HTTP_201_CREATED)
async def start_instance(
    challenge_id: str,
    user: CurrentUser,
    database: DatabaseDep,
    settings: SettingsDep,
    docker: DockerDep,
) -> InstancePublic:
    _expire_instances(user, database, docker)
    with database.connect() as connection:
        challenge = connection.execute(
            "SELECT * FROM challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
        existing = connection.execute(
            "SELECT id FROM instances WHERE user_id = ? AND challenge_id = ? "
            "AND status IN ('starting', 'running')",
            (user["id"], challenge_id),
        ).fetchone()
    if not challenge or (challenge["status"] != "published" and user["role"] != "admin"):
        raise HTTPException(status_code=404, detail="Challenge not found")
    if existing:
        raise HTTPException(status_code=409, detail="An active instance already exists for this challenge")
    if not challenge["docker_image"] or not challenge["internal_port"]:
        raise HTTPException(status_code=409, detail="This challenge has no deployable Docker environment")

    instance_id = str(uuid.uuid4())
    container_name = f"sycl-{instance_id.replace('-', '')[:20]}"
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.instance_ttl_minutes)
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO instances (
                id, user_id, challenge_id, container_name, status, expires_at, created_at
            ) VALUES (?, ?, ?, ?, 'starting', ?, ?)
            """,
            (instance_id, user["id"], challenge_id, container_name, expires_at.isoformat(), now.isoformat()),
        )

    try:
        started = docker.start(
            image=challenge["docker_image"],
            internal_port=challenge["internal_port"],
            name=container_name,
            user_id=user["id"],
            challenge_id=challenge_id,
        )
    except ContainerError as exc:
        with database.connect() as connection:
            connection.execute(
                "UPDATE instances SET status = 'failed', error_message = ? WHERE id = ?",
                (str(exc), instance_id),
            )
        raise HTTPException(status_code=503, detail=f"Container failed to start: {exc}") from exc

    with database.connect() as connection:
        connection.execute(
            "UPDATE instances SET container_id = ?, public_host = ?, public_port = ?, status = 'running' "
            "WHERE id = ?",
            (started.container_id, settings.instance_public_host, started.public_port, instance_id),
        )
        row = connection.execute(
            "SELECT i.*, c.title AS challenge_title FROM instances i JOIN challenges c ON c.id = i.challenge_id "
            "WHERE i.id = ?",
            (instance_id,),
        ).fetchone()
    return _serialize(row)


@router.delete("/{instance_id}", response_model=Message)
async def stop_instance(
    instance_id: str, user: CurrentUser, database: DatabaseDep, docker: DockerDep
) -> Message:
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM instances WHERE id = ?", (instance_id,)).fetchone()
    if not row or (row["user_id"] != user["id"] and user["role"] != "admin"):
        raise HTTPException(status_code=404, detail="Instance not found")
    if row["status"] in {"stopped", "failed"}:
        return Message(message="Instance is already stopped")
    error = None
    if row["container_id"]:
        try:
            docker.stop(row["container_id"])
        except ContainerError as exc:
            error = str(exc)
    with database.connect() as connection:
        connection.execute(
            "UPDATE instances SET status = 'stopped', stopped_at = ?, error_message = COALESCE(?, error_message) "
            "WHERE id = ?",
            (datetime.now(UTC).isoformat(), error, instance_id),
        )
    if error:
        raise HTTPException(status_code=502, detail=f"Container stop reported an error: {error}")
    return Message(message="Instance stopped")
