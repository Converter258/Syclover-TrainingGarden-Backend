import sqlite3

from app.core.database import Database


def test_alpha_migration_preserves_legacy_rows(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE challenges (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL, category TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('ctf', 'awdp')),
            difficulty TEXT NOT NULL CHECK (difficulty IN ('easy', 'medium', 'hard')),
            points INTEGER NOT NULL, docker_image TEXT, internal_port INTEGER,
            flag_digest TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'archived')),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE assets (
            id TEXT PRIMARY KEY, challenge_id TEXT NOT NULL REFERENCES challenges(id),
            user_id TEXT, kind TEXT NOT NULL CHECK (kind IN ('attachment', 'patch', 'fix_script')),
            original_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            validation_status TEXT NOT NULL CHECK (validation_status IN ('pending', 'valid', 'invalid')),
            validation_output TEXT, created_at TEXT NOT NULL
        );
        INSERT INTO challenges VALUES (
            'legacy', 'Legacy', 'legacy', 'Legacy challenge description', 'Web', 'ctf',
            'medium', 100, NULL, NULL, 'digest', 'published', 'now', 'now'
        );
        """
    )
    connection.close()

    database = Database(path)
    database.initialize()
    with database.connect() as migrated:
        assert migrated.execute(
            "SELECT difficulty FROM challenges WHERE id = 'legacy'"
        ).fetchone()["difficulty"] == "normal"
        migrated.execute(
            """
            INSERT INTO assets VALUES (
                'script', 'legacy', NULL, 'check_script', 'check.sh', 'legacy/check.sh',
                10, 'valid', 'ok', 'now'
            )
            """
        )
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
