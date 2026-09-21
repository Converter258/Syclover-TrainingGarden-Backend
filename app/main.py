from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, awdp, challenges, instances, scoreboard, users
from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.seed import seed_database
from app.services.docker import DockerService

VERSION = "Alpha0.0.1"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database = Database(resolved_settings.database_path)
        database.initialize()
        seed_database(database, resolved_settings)
        resolved_settings.storage_path.mkdir(parents=True, exist_ok=True)
        application.state.settings = resolved_settings
        application.state.database = database
        application.state.docker = DockerService(resolved_settings.docker_mode)
        yield

    application = FastAPI(
        title=resolved_settings.app_name,
        version=VERSION,
        description="CTF and AWDP training platform API",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api_prefix = "/api/v1"
    application.include_router(auth.router, prefix=api_prefix)
    application.include_router(users.router, prefix=api_prefix)
    application.include_router(challenges.router, prefix=api_prefix)
    application.include_router(instances.router, prefix=api_prefix)
    application.include_router(awdp.router, prefix=api_prefix)
    application.include_router(scoreboard.router, prefix=api_prefix)

    @application.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
