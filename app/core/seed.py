from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.config import Settings
from app.core.database import Database
from app.core.security import digest_flag, hash_password
from app.services.assets import store_bytes, validate_asset

DEMO_CHALLENGES = (
    {
        "title": "Welcome Header",
        "slug": "welcome-header",
        "description": "Inspect the service response carefully and recover the flag hidden in its metadata.",
        "category": "Web",
        "mode": "ctf",
        "difficulty": "easy",
        "points": 100,
        "docker_image": "nginx:alpine",
        "internal_port": 80,
        "flag": "SYC{welcome_to_training_garden}",
    },
    {
        "title": "Broken Profile",
        "slug": "broken-profile",
        "description": "Audit the profile endpoint and identify the authorization boundary that can be bypassed.",
        "category": "Web",
        "mode": "ctf",
        "difficulty": "normal",
        "points": 250,
        "docker_image": None,
        "internal_port": None,
        "flag": "SYC{profile_boundary_found}",
    },
    {
        "title": "Service Under Fire",
        "slug": "service-under-fire",
        "description": "Patch the vulnerable service, pass the health check, and keep the service available.",
        "category": "Pwn",
        "mode": "awdp",
        "difficulty": "hard",
        "points": 500,
        "docker_image": "nginx:alpine",
        "internal_port": 80,
        "flag": "SYC{defense_is_an_engineering_discipline}",
    },
)

DEMO_AWDP_SCRIPTS = {
    "check_script": ("check.sh", b"#!/bin/sh\ntest -f /etc/nginx/nginx.conf\n"),
    "fix_script": ("fix.sh", b"#!/bin/sh\nnginx -t && nginx -s reload\n"),
}


def seed_database(database: Database, settings: Settings) -> None:
    now = datetime.now(UTC).isoformat()
    with database.connect() as connection:
        if settings.admin_password:
            existing_admin = connection.execute(
                "SELECT id FROM users WHERE username = ?", (settings.admin_username,)
            ).fetchone()
            if not existing_admin:
                connection.execute(
                    "INSERT INTO users (id, username, password_hash, role, is_active, created_at) "
                    "VALUES (?, ?, ?, 'admin', 1, ?)",
                    (
                        str(uuid.uuid4()),
                        settings.admin_username,
                        hash_password(settings.admin_password),
                        now,
                    ),
                )

        if not settings.seed_demo:
            return
        for challenge in DEMO_CHALLENGES:
            existing = connection.execute(
                "SELECT id FROM challenges WHERE slug = ?", (challenge["slug"],)
            ).fetchone()
            challenge_id = existing["id"] if existing else str(uuid.uuid4())
            if not existing:
                connection.execute(
                    """
                    INSERT INTO challenges (
                        id, title, slug, description, category, mode, difficulty, points,
                        docker_image, internal_port, flag_digest, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', ?, ?)
                    """,
                    (
                        challenge_id,
                        challenge["title"],
                        challenge["slug"],
                        challenge["description"],
                        challenge["category"],
                        challenge["mode"],
                        challenge["difficulty"],
                        challenge["points"],
                        challenge["docker_image"],
                        challenge["internal_port"],
                        digest_flag(challenge["flag"], settings.secret_key),
                        now,
                        now,
                    ),
                )
            if challenge["mode"] == "awdp":
                for kind, (filename, content) in DEMO_AWDP_SCRIPTS.items():
                    if connection.execute(
                        "SELECT 1 FROM assets WHERE challenge_id = ? AND kind = ? AND validation_status = 'valid'",
                        (challenge_id, kind),
                    ).fetchone():
                        continue
                    stored_name, path = store_bytes(
                        settings.storage_path, challenge_id, filename, content
                    )
                    valid, output = validate_asset(path, kind)
                    connection.execute(
                        """
                        INSERT INTO assets (
                            id, challenge_id, user_id, kind, original_name, stored_name,
                            size_bytes, validation_status, validation_output, created_at
                        ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()), challenge_id, kind, filename, stored_name,
                            len(content), "valid" if valid else "invalid", output, now,
                        ),
                    )
