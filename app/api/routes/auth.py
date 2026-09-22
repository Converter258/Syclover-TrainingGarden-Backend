from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DatabaseDep, SettingsDep
from app.core.security import create_access_token, hash_password, verify_password
from app.services.achievements import grant_achievement, user_achievement_slugs
from app.services.invites import claim_invite_code, find_invite_code
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserPublic

router = APIRouter(prefix="/auth", tags=["auth"])


def _public_user(row: dict | sqlite3.Row, connection: sqlite3.Connection | None = None) -> UserPublic:
    data = dict(row)
    data["is_active"] = bool(data["is_active"])
    if connection is not None:
        data["achievement_slugs"] = user_achievement_slugs(connection, data["id"])
    return UserPublic.model_validate(data)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, database: DatabaseDep, settings: SettingsDep) -> TokenResponse:
    """Create a player account against a single-use invitation code.

    The code lookup, the account insert and the code claim share one transaction,
    so a rejected registration (unknown code, taken username, race on the last
    use) never burns the code and a code can never fund two accounts.
    """
    now = datetime.now(UTC).isoformat()
    user_id = str(uuid.uuid4())
    with database.connect() as connection:
        invite = find_invite_code(connection, payload.invite_code)
        if invite is None:
            raise HTTPException(status_code=400, detail="Invitation code is not valid")
        if invite["used_at"]:
            raise HTTPException(status_code=409, detail="Invitation code has already been used")
        try:
            connection.execute(
                "INSERT INTO users (id, username, password_hash, role, is_active, created_at) "
                "VALUES (?, ?, ?, 'player', 1, ?)",
                (user_id, payload.username, hash_password(payload.password), now),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Username is already registered") from exc
        if not claim_invite_code(connection, invite["id"], user_id):
            raise HTTPException(status_code=409, detail="Invitation code has already been used")
        grant_achievement(connection, user_id, "sprout_member")
        row = connection.execute(
            "SELECT id, username, role, is_active, avatar_url, signature, direction, created_at "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        user = _public_user(row, connection)
    return TokenResponse(
        access_token=create_access_token(user.id, user.role, settings.secret_key, settings.token_ttl_minutes),
        user=user,
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, database: DatabaseDep, settings: SettingsDep) -> TokenResponse:
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE username = ?", (payload.username,)).fetchone()
        password_valid = bool(row and verify_password(payload.password, row["password_hash"]))
        if not row or not row["is_active"] or not password_valid:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        user = _public_user(row, connection)
    return TokenResponse(
        access_token=create_access_token(user.id, user.role, settings.secret_key, settings.token_ttl_minutes),
        user=user,
    )


@router.get("/me", response_model=UserPublic)
async def me(user: CurrentUser, database: DatabaseDep) -> UserPublic:
    with database.connect() as connection:
        return _public_user(user, connection)
