from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import AdminUser, DatabaseDep
from app.schemas import InviteCodeCreate, InviteCodePublic, Message
from app.services.invites import (
    INVITE_SELECT,
    create_invite_codes,
    revoke_invite_code,
    serialize_invite,
)

router = APIRouter(prefix="/invites", tags=["invites"])


@router.post("", response_model=list[InviteCodePublic], status_code=status.HTTP_201_CREATED)
async def create_invite_codes_endpoint(
    payload: InviteCodeCreate,
    admin: AdminUser,
    database: DatabaseDep,
) -> list[InviteCodePublic]:
    """Issue one-time registration codes; administrators and root administrators only."""
    with database.connect() as connection:
        codes = create_invite_codes(
            connection, count=payload.count, note=payload.note, created_by=admin["id"]
        )
        placeholders = ", ".join("?" for _ in codes)
        rows = connection.execute(
            f"{INVITE_SELECT} WHERE i.code IN ({placeholders}) ORDER BY i.code",
            tuple(codes),
        ).fetchall()
    return [InviteCodePublic.model_validate(serialize_invite(row)) for row in rows]


@router.get("", response_model=list[InviteCodePublic])
async def list_invite_codes(
    _: AdminUser,
    database: DatabaseDep,
    state: Literal["all", "unused", "used"] = Query(default="all"),
) -> list[InviteCodePublic]:
    filters = {"unused": "WHERE i.used_at IS NULL", "used": "WHERE i.used_at IS NOT NULL"}
    with database.connect() as connection:
        rows = connection.execute(
            f"{INVITE_SELECT} {filters.get(state, '')} ORDER BY i.created_at DESC, i.code ASC"
        ).fetchall()
    return [InviteCodePublic.model_validate(serialize_invite(row)) for row in rows]


@router.delete("/{invite_id}", response_model=Message)
async def delete_invite_code(invite_id: str, _: AdminUser, database: DatabaseDep) -> Message:
    """Revoke an unused code. Redeemed codes stay as the registration audit trail."""
    with database.connect() as connection:
        if revoke_invite_code(connection, invite_id):
            return Message(message="Invitation code revoked")
        still_listed = (
            connection.execute("SELECT 1 FROM invite_codes WHERE id = ?", (invite_id,)).fetchone()
            is not None
        )
    if not still_listed:
        raise HTTPException(status_code=404, detail="Invitation code not found")
    raise HTTPException(
        status_code=409, detail="A redeemed invitation code is kept for auditing"
    )
