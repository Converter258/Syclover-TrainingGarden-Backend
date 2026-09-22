from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.api.deps import CurrentUser, DatabaseDep, DockerDep, SettingsDep
from app.schemas import InstancePublic, Message
from app.services.docker import FLAG_PATTERN, ContainerError
from app.services.flags import render_instance_flag
from app.services.reaper import expire_instances

router = APIRouter(prefix="/instances", tags=["instances"])
logger = logging.getLogger("syclover.instances")

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}


def _client_host(request: Request) -> str | None:
    """Hostname the browser used to reach the platform, honouring a trusted proxy."""
    forwarded = request.headers.get("x-forwarded-host")
    candidate = (forwarded.split(",")[0] if forwarded else request.headers.get("host", "")).strip()
    if not candidate:
        return None
    if candidate.startswith("["):
        return candidate.split("]")[0].lstrip("[") or None
    return candidate.rsplit(":", 1)[0] or None


def _effective_public_host(request: Request, settings) -> str:
    """Address players should connect challenge instances on.

    The container ports are published on ``SYCL_INSTANCE_BIND_ADDRESS``, so the reported
    address must match it or the UI would advertise a link that cannot work:

    * a wildcard bind means "any interface", so the platform reuses the hostname the
      player reached the platform on;
    * a concrete bind address is reported as-is;
    * otherwise the configured ``SYCL_INSTANCE_PUBLIC_HOST`` is used.
    """
    configured = (settings.instance_public_host or "").strip()
    bind = (settings.instance_bind_address or "").strip()
    if bind in {"0.0.0.0", "::", "[::]"}:
        return _client_host(request) or configured or "localhost"
    if bind:
        return bind
    return configured or _client_host(request) or "localhost"


def _serialize(row, user: dict | None = None, public_host: str | None = None) -> InstancePublic:
    data = dict(row)
    if public_host and data.get("public_port"):
        data["public_host"] = public_host
    if user is not None:
        data["mine"] = data.get("user_id") == user["id"]
        data["is_admin"] = user["role"] == "admin"
    return InstancePublic.model_validate(data)


def _launch_instance(instance_id: str, public_host: str, launch: dict, database, docker) -> None:
    """Start one challenge container outside the request/response cycle."""
    try:
        started = docker.start(**launch)
    except ContainerError as exc:
        logger.warning("Instance %s failed to start: %s", instance_id, exc)
        with database.connect() as connection:
            connection.execute(
                "UPDATE instances SET status = 'failed', error_message = ? "
                "WHERE id = ? AND status = 'starting'",
                (str(exc), instance_id),
            )
        return
    with database.connect() as connection:
        connection.execute(
            "UPDATE instances SET container_id = ?, public_host = ?, public_port = ?, status = 'running' "
            "WHERE id = ? AND status = 'starting'",
            (started.container_id, public_host, started.public_port, instance_id),
        )


def _reclaim(database, docker) -> None:
    """Reclaim expired instances before answering a request, for all users."""
    expire_instances(database, docker)


def _resync_ports(user: dict, database, docker, settings) -> None:
    """Refresh published ports when a container was restarted outside the platform."""
    with database.connect() as connection:
        condition = "" if user["role"] == "admin" else "AND i.user_id = ?"
        values = () if user["role"] == "admin" else (user["id"],)
        rows = connection.execute(
            f"""
            SELECT i.id, i.container_id, i.public_port, c.internal_port
            FROM instances i JOIN challenges c ON c.id = i.challenge_id
            WHERE i.status = 'running' AND i.container_id IS NOT NULL
              AND c.internal_port IS NOT NULL {condition}
            """,
            values,
        ).fetchall()
    for row in rows:
        current = docker.container_port(row["container_id"], row["internal_port"])
        if current is None or current == row["public_port"]:
            continue
        with database.connect() as connection:
            connection.execute(
                "UPDATE instances SET public_port = ? WHERE id = ? AND status = 'running'",
                (current, row["id"]),
            )
        logger.info(
            "Instance %s port refreshed: %s -> %s", row["id"], row["public_port"], current
        )


def _reflect_dead_containers(user: dict, database, docker) -> None:
    """Mark instances whose container vanished without the platform stopping it."""
    with database.connect() as connection:
        if user["role"] == "admin":
            rows = connection.execute(
                "SELECT id, container_id FROM instances WHERE status = 'running'"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id, container_id FROM instances WHERE status = 'running' AND user_id = ?",
                (user["id"],),
            ).fetchall()
    for row in rows:
        if not row["container_id"] or docker.container_exists(row["container_id"]):
            continue
        with database.connect() as connection:
            connection.execute(
                "UPDATE instances SET status = 'failed', stopped_at = ?, "
                "error_message = COALESCE(error_message, 'Container exited unexpectedly') "
                "WHERE id = ? AND status = 'running'",
                (datetime.now(UTC).isoformat(), row["id"]),
            )


@router.get("", response_model=list[InstancePublic])
async def list_instances(
    request: Request,
    user: CurrentUser,
    database: DatabaseDep,
    docker: DockerDep,
    settings: SettingsDep,
) -> list[InstancePublic]:
    _reclaim(database, docker)
    _reflect_dead_containers(user, database, docker)
    _resync_ports(user, database, docker, settings)
    public_host = _effective_public_host(request, settings)
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
    return [_serialize(row, user, public_host) for row in rows]


@router.post("/{challenge_id}", response_model=InstancePublic, status_code=status.HTTP_201_CREATED)
async def start_instance(
    challenge_id: str,
    request: Request,
    user: CurrentUser,
    database: DatabaseDep,
    settings: SettingsDep,
    docker: DockerDep,
    background: BackgroundTasks,
) -> InstancePublic:
    _reclaim(database, docker)
    _reflect_dead_containers(user, database, docker)
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
    instance_flag = render_instance_flag(
        challenge["flag_template"],
        dynamic=bool(challenge["dynamic_flag"]),
        default_prefix=settings.flag_prefix,
    )
    if not FLAG_PATTERN.fullmatch(instance_flag):
        # Never let a malformed flag fail the whole start with an opaque container error:
        # fall back to the literal template, then explain what is wrong with it.
        fallback = (challenge["flag_template"] or "").strip()
        if fallback and FLAG_PATTERN.fullmatch(fallback):
            logger.warning(
                "Challenge %s produced an invalid dynamic flag; using the literal template",
                challenge_id,
            )
            instance_flag = fallback
        else:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This challenge has no usable flag: set a flag like SYC{...} - the "
                    "dynamic switch replaces the RAND token with a random value per instance"
                ),
            )
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.instance_ttl_minutes)
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO instances (
                id, user_id, challenge_id, container_name, instance_flag, status, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, 'starting', ?, ?)
            """,
            (
                instance_id,
                user["id"],
                challenge_id,
                container_name,
                instance_flag,
                expires_at.isoformat(),
                now.isoformat(),
            ),
        )

    launch = dict(
        image=challenge["docker_image"],
        internal_port=challenge["internal_port"],
        name=container_name,
        user_id=user["id"],
        challenge_id=challenge_id,
        bind_address=settings.instance_bind_address,
        port_range=settings.instance_port_range,
        flag=instance_flag,
    )
    public_host = _effective_public_host(request, settings)

    if settings.instance_start_async:
        # Answer immediately so the UI can show live startup progress instead of
        # holding one HTTP request open for the whole container start.
        background.add_task(_launch_instance, instance_id, public_host, launch, database, docker)
        with database.connect() as connection:
            row = connection.execute(
                "SELECT i.*, c.title AS challenge_title FROM instances i "
                "JOIN challenges c ON c.id = i.challenge_id WHERE i.id = ?",
                (instance_id,),
            ).fetchone()
        return _serialize(row, user, public_host)

    try:
        started = docker.start(**launch)
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
            (started.container_id, public_host, started.public_port, instance_id),
        )
        row = connection.execute(
            "SELECT i.*, c.title AS challenge_title FROM instances i JOIN challenges c ON c.id = i.challenge_id "
            "WHERE i.id = ?",
            (instance_id,),
        ).fetchone()
    return _serialize(row, user, public_host)


@router.get("/{instance_id}", response_model=InstancePublic)
async def get_instance(
    instance_id: str,
    request: Request,
    user: CurrentUser,
    database: DatabaseDep,
    settings: SettingsDep,
    docker: DockerDep,
) -> InstancePublic:
    """Single instance status, used by the UI while a container is starting."""
    _resync_ports(user, database, docker, settings)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT i.*, c.title AS challenge_title FROM instances i JOIN challenges c ON c.id = i.challenge_id "
            "WHERE i.id = ?",
            (instance_id,),
        ).fetchone()
    if not row or (row["user_id"] != user["id"] and user["role"] != "admin"):
        raise HTTPException(status_code=404, detail="Instance not found")
    return _serialize(row, user, _effective_public_host(request, settings))


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
