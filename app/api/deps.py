from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.database import Database
from app.core.security import InvalidTokenError, decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


async def get_database(request: Request) -> Database:
    return request.app.state.database


async def get_settings_dependency(request: Request) -> Settings:
    return request.app.state.settings


async def get_docker_service(request: Request):
    return request.app.state.docker


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings_dependency)],
) -> dict:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload = decode_access_token(credentials.credentials, settings.secret_key)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    with database.connect() as connection:
        row = connection.execute(
            "SELECT id, username, role, is_active, created_at FROM users WHERE id = ?",
            (payload["sub"],),
        ).fetchone()
    if not row or not row["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is unavailable")
    return dict(row)


async def admin_user(user: Annotated[dict, Depends(current_user)]) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required")
    return user


CurrentUser = Annotated[dict, Depends(current_user)]
AdminUser = Annotated[dict, Depends(admin_user)]
DatabaseDep = Annotated[Database, Depends(get_database)]
SettingsDep = Annotated[Settings, Depends(get_settings_dependency)]
DockerDep = Annotated[object, Depends(get_docker_service)]
