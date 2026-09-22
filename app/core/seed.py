from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.config import Settings
from app.core.database import Database
from app.core.security import hash_password


def seed_database(database: Database, settings: Settings) -> None:
    now = datetime.now(UTC).isoformat()
    root_username = "Syclover"
    with database.connect() as connection:
        if settings.admin_password:
            existing_admin = connection.execute(
                "SELECT id FROM users WHERE username = ?", (root_username,)
            ).fetchone()
            if existing_admin:
                connection.execute(
                    "UPDATE users SET role = 'root_admin', is_active = 1 WHERE id = ?",
                    (existing_admin["id"],),
                )
            else:
                legacy_admin = connection.execute(
                    "SELECT id FROM users WHERE username = 'admin' AND role IN ('admin', 'root_admin')"
                ).fetchone()
                if legacy_admin:
                    connection.execute(
                        "UPDATE users SET username = ?, role = 'root_admin', is_active = 1 WHERE id = ?",
                        (root_username, legacy_admin["id"]),
                    )
                    existing_admin = legacy_admin
                else:
                    connection.execute(
                        "INSERT INTO users (id, username, password_hash, role, is_active, created_at) "
                        "VALUES (?, ?, ?, 'root_admin', 1, ?)",
                        (
                            str(uuid.uuid4()),
                            root_username,
                            hash_password(settings.admin_password),
                            now,
                        ),
                    )
            connection.execute(
                "UPDATE users SET role = 'admin' WHERE role = 'root_admin' AND username <> ?",
                (root_username,),
            )
