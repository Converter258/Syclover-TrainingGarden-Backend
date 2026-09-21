from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, DatabaseDep, DockerDep, SettingsDep
from app.schemas import Message, UserPublic, UserUpdate
from app.services.docker import ContainerError

router = APIRouter(prefix="/users", tags=["users"])


def _serialize(row) -> UserPublic:
    data = dict(row)
    data["is_active"] = bool(data["is_active"])
    return UserPublic.model_validate(data)


@router.get("", response_model=list[UserPublic])
async def list_users(_: AdminUser, database: DatabaseDep) -> list[UserPublic]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, username, role, is_active, created_at FROM users ORDER BY created_at DESC"
        ).fetchall()
    return [_serialize(row) for row in rows]


@router.patch("/{user_id}", response_model=UserPublic)
async def update_user(user_id: str, payload: UserUpdate, admin: AdminUser, database: DatabaseDep) -> UserPublic:
    if admin["id"] == user_id and payload.is_active is False:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No changes supplied")
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = [int(value) if isinstance(value, bool) else value for value in fields.values()]
    with database.connect() as connection:
        cursor = connection.execute(
            f"UPDATE users SET {assignments} WHERE id = ?", (*values, user_id)
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found")
        row = connection.execute(
            "SELECT id, username, role, is_active, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return _serialize(row)


@router.delete("/{user_id}", response_model=Message)
async def delete_user(
    user_id: str,
    admin: AdminUser,
    database: DatabaseDep,
    settings: SettingsDep,
    docker: DockerDep,
) -> Message:
    if admin["id"] == user_id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    with database.connect() as connection:
        user = connection.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        instances = connection.execute(
            "SELECT container_id FROM instances WHERE user_id = ? AND status IN ('starting', 'running')",
            (user_id,),
        ).fetchall()
        assets = connection.execute(
            "SELECT stored_name FROM assets WHERE user_id = ? AND kind = 'patch'", (user_id,)
        ).fetchall()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    for instance in instances:
        if instance["container_id"]:
            try:
                docker.stop(instance["container_id"])
            except ContainerError:
                pass
    with database.connect() as connection:
        connection.execute("DELETE FROM defense_solves WHERE user_id = ?", (user_id,))
        connection.execute(
            "DELETE FROM deployment_events WHERE user_id = ? OR instance_id IN "
            "(SELECT id FROM instances WHERE user_id = ?) OR asset_id IN "
            "(SELECT id FROM assets WHERE user_id = ? AND kind = 'patch')",
            (user_id, user_id, user_id),
        )
        connection.execute("DELETE FROM submissions WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM instances WHERE user_id = ?", (user_id,))
        connection.execute(
            "UPDATE assets SET user_id = NULL WHERE user_id = ? "
            "AND kind IN ('check_script', 'fix_script')",
            (user_id,),
        )
        connection.execute("DELETE FROM assets WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
    for asset in assets:
        (settings.storage_path / asset["stored_name"]).unlink(missing_ok=True)
    return Message(message="User deleted")
