from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'player' CHECK (role IN ('player', 'admin')),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS challenges (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('ctf', 'awdp')),
    difficulty TEXT NOT NULL CHECK (difficulty IN ('noob', 'easy', 'normal', 'hard', 'insane')),
    points INTEGER NOT NULL CHECK (points > 0),
    docker_image TEXT,
    internal_port INTEGER,
    flag_digest TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instances (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    challenge_id TEXT NOT NULL REFERENCES challenges(id),
    container_id TEXT,
    container_name TEXT NOT NULL,
    public_host TEXT,
    public_port INTEGER,
    status TEXT NOT NULL CHECK (status IN ('starting', 'running', 'stopped', 'failed')),
    error_message TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    stopped_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_instances_user_status ON instances(user_id, status);

CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    challenge_id TEXT NOT NULL REFERENCES challenges(id),
    correct INTEGER NOT NULL,
    awarded_points INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_submissions_score ON submissions(user_id, correct);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    challenge_id TEXT NOT NULL REFERENCES challenges(id),
    user_id TEXT REFERENCES users(id),
    kind TEXT NOT NULL CHECK (kind IN ('attachment', 'patch', 'check_script', 'fix_script')),
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (validation_status IN ('pending', 'valid', 'invalid')),
    validation_output TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deployment_events (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES instances(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    success INTEGER NOT NULL,
    output TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_constraints()
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def _migrate_legacy_constraints(self) -> None:
        """Rebuild constrained SQLite tables introduced before Alpha0.0.1."""
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            challenge_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'challenges'"
            ).fetchone()
            if challenge_sql and "'normal'" not in challenge_sql[0]:
                connection.executescript(
                    """
                    CREATE TABLE challenges_v2 (
                        id TEXT PRIMARY KEY, title TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
                        description TEXT NOT NULL, category TEXT NOT NULL,
                        mode TEXT NOT NULL CHECK (mode IN ('ctf', 'awdp')),
                        difficulty TEXT NOT NULL CHECK (difficulty IN ('noob', 'easy', 'normal', 'hard', 'insane')),
                        points INTEGER NOT NULL CHECK (points > 0), docker_image TEXT,
                        internal_port INTEGER, flag_digest TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    INSERT INTO challenges_v2
                    SELECT id, title, slug, description, category, mode,
                           CASE difficulty WHEN 'medium' THEN 'normal' ELSE difficulty END,
                           points, docker_image, internal_port, flag_digest, status, created_at, updated_at
                    FROM challenges;
                    DROP TABLE challenges;
                    ALTER TABLE challenges_v2 RENAME TO challenges;
                    """
                )

            asset_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'assets'"
            ).fetchone()
            if asset_sql and "'check_script'" not in asset_sql[0]:
                connection.executescript(
                    """
                    CREATE TABLE assets_v2 (
                        id TEXT PRIMARY KEY,
                        challenge_id TEXT NOT NULL REFERENCES challenges(id),
                        user_id TEXT REFERENCES users(id),
                        kind TEXT NOT NULL CHECK (kind IN ('attachment', 'patch', 'check_script', 'fix_script')),
                        original_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE,
                        size_bytes INTEGER NOT NULL,
                        validation_status TEXT NOT NULL DEFAULT 'pending'
                            CHECK (validation_status IN ('pending', 'valid', 'invalid')),
                        validation_output TEXT, created_at TEXT NOT NULL
                    );
                    INSERT INTO assets_v2 SELECT * FROM assets;
                    DROP TABLE assets;
                    ALTER TABLE assets_v2 RENAME TO assets;
                    """
                )
            connection.commit()
        finally:
            connection.close()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
