from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.deps import AdminUser, CurrentUser, DatabaseDep, DockerDep, SettingsDep
from app.schemas import AssetPublic, DeploymentEventPublic
from app.services.assets import safe_filename, store_bytes, validate_asset
from app.services.docker import ContainerError

router = APIRouter(prefix="/awdp", tags=["awdp"])


def _asset(row) -> AssetPublic:
    return AssetPublic.model_validate(
        {**dict(row), "download_url": f"/api/v1/challenges/assets/{row['id']}/download"}
    )


@router.get("/assets", response_model=list[AssetPublic])
async def list_defense_assets(user: CurrentUser, database: DatabaseDep) -> list[AssetPublic]:
    with database.connect() as connection:
        if user["role"] == "admin":
            rows = connection.execute(
                "SELECT * FROM assets WHERE kind = 'patch' ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM assets WHERE user_id = ? AND kind = 'patch' "
                "ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
    return [_asset(row) for row in rows]


@router.get("/{challenge_id}/scripts", response_model=list[AssetPublic])
async def list_challenge_scripts(
    challenge_id: str, _: AdminUser, database: DatabaseDep
) -> list[AssetPublic]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM assets WHERE challenge_id = ? "
            "AND kind IN ('check_script', 'fix_script') ORDER BY created_at DESC",
            (challenge_id,),
        ).fetchall()
    return [_asset(row) for row in rows]


@router.post("/{challenge_id}/scripts", response_model=AssetPublic, status_code=status.HTTP_201_CREATED)
async def upload_challenge_script(
    challenge_id: str,
    request: Request,
    admin: AdminUser,
    database: DatabaseDep,
    settings: SettingsDep,
    kind: str = Query(pattern="^(check_script|fix_script)$"),
    filename: str = Query(min_length=1, max_length=120),
) -> AssetPublic:
    content = await request.body()
    if not content or len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Script is empty or exceeds the upload limit")
    with database.connect() as connection:
        challenge = connection.execute(
            "SELECT mode FROM challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
    if not challenge or challenge["mode"] != "awdp":
        raise HTTPException(status_code=404, detail="AWDP challenge not found")
    stored_name, path = store_bytes(settings.storage_path, challenge_id, filename, content)
    valid, output = validate_asset(path, kind)
    asset_id = str(uuid.uuid4())
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO assets (
                id, challenge_id, user_id, kind, original_name, stored_name, size_bytes,
                validation_status, validation_output, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id, challenge_id, admin["id"], kind, safe_filename(filename), stored_name,
                len(content), "valid" if valid else "invalid", output[:10_000],
                datetime.now(UTC).isoformat(),
            ),
        )
        row = connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return _asset(row)


@router.post("/{challenge_id}/assets", response_model=AssetPublic, status_code=status.HTTP_201_CREATED)
async def upload_defense_asset(
    challenge_id: str,
    request: Request,
    user: CurrentUser,
    database: DatabaseDep,
    settings: SettingsDep,
    kind: str = Query(default="patch", pattern="^patch$"),
    filename: str = Query(min_length=1, max_length=120),
) -> AssetPublic:
    content = await request.body()
    if not content or len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="File is empty or exceeds the upload limit")
    with database.connect() as connection:
        challenge = connection.execute(
            "SELECT mode, status FROM challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
    if not challenge or challenge["mode"] != "awdp" or (
        challenge["status"] != "published" and user["role"] != "admin"
    ):
        raise HTTPException(status_code=404, detail="AWDP challenge not found")

    stored_name, path = store_bytes(settings.storage_path, challenge_id, filename, content)
    valid, output = validate_asset(path, "patch")
    asset_id = str(uuid.uuid4())
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO assets (
                id, challenge_id, user_id, kind, original_name, stored_name, size_bytes,
                validation_status, validation_output, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                challenge_id,
                user["id"],
                "patch",
                safe_filename(filename),
                stored_name,
                len(content),
                "valid" if valid else "invalid",
                output[:10_000],
                datetime.now(UTC).isoformat(),
            ),
        )
        row = connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return _asset(row)


@router.post("/instances/{instance_id}/deploy/{asset_id}", response_model=DeploymentEventPublic)
async def deploy_defense_asset(
    instance_id: str,
    asset_id: str,
    user: CurrentUser,
    database: DatabaseDep,
    settings: SettingsDep,
    docker: DockerDep,
) -> DeploymentEventPublic:
    with database.connect() as connection:
        instance = connection.execute(
            "SELECT * FROM instances WHERE id = ?", (instance_id,)
        ).fetchone()
        asset = connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        check_script = connection.execute(
            "SELECT * FROM assets WHERE challenge_id = ? AND kind = 'check_script' "
            "AND validation_status = 'valid' ORDER BY created_at DESC LIMIT 1",
            (instance["challenge_id"],),
        ).fetchone() if instance else None
    if not instance or (instance["user_id"] != user["id"] and user["role"] != "admin"):
        raise HTTPException(status_code=404, detail="Instance not found")
    if not asset or (asset["user_id"] != user["id"] and user["role"] != "admin"):
        raise HTTPException(status_code=404, detail="Defense asset not found")
    if instance["challenge_id"] != asset["challenge_id"]:
        raise HTTPException(status_code=400, detail="Asset and instance belong to different challenges")
    if instance["status"] != "running" or not instance["container_id"]:
        raise HTTPException(status_code=409, detail="Instance is not running")
    if asset["validation_status"] != "valid":
        raise HTTPException(status_code=422, detail="Asset did not pass validation")
    if asset["kind"] != "patch":
        raise HTTPException(status_code=422, detail="Only patch files can be deployed by participants")
    if not check_script:
        raise HTTPException(status_code=422, detail="This AWDP challenge has no valid Check script")

    success = True
    try:
        patch_output = docker.apply_asset(
            instance["container_id"],
            settings.storage_path / asset["stored_name"],
            asset["kind"],
        )
        check_output = docker.apply_asset(
            instance["container_id"],
            settings.storage_path / check_script["stored_name"],
            "check_script",
        )
        output = f"Patch output:\n{patch_output or 'Applied successfully.'}\n\nCheck output:\n{check_output or 'Check passed.'}"
    except ContainerError as exc:
        success = False
        output = str(exc)
    event_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO deployment_events (id, instance_id, asset_id, user_id, success, output, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, instance_id, asset_id, user["id"], int(success), output[:20_000], created_at),
        )
        row = connection.execute(
            "SELECT id, instance_id, asset_id, success, output, created_at FROM deployment_events WHERE id = ?",
            (event_id,),
        ).fetchone()
    return DeploymentEventPublic.model_validate({**dict(row), "success": bool(row["success"])})


@router.get("/instances/{instance_id}/events", response_model=list[DeploymentEventPublic])
async def list_deployment_events(
    instance_id: str, user: CurrentUser, database: DatabaseDep
) -> list[DeploymentEventPublic]:
    with database.connect() as connection:
        instance = connection.execute(
            "SELECT user_id FROM instances WHERE id = ?", (instance_id,)
        ).fetchone()
        if not instance or (instance["user_id"] != user["id"] and user["role"] != "admin"):
            raise HTTPException(status_code=404, detail="Instance not found")
        rows = connection.execute(
            "SELECT id, instance_id, asset_id, success, output, created_at FROM deployment_events "
            "WHERE instance_id = ? ORDER BY created_at DESC",
            (instance_id,),
        ).fetchall()
    return [
        DeploymentEventPublic.model_validate({**dict(row), "success": bool(row["success"])})
        for row in rows
    ]
