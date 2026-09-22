from __future__ import annotations

import sqlite3


def user_achievement_slugs(connection: sqlite3.Connection, user_id: str) -> list[str]:
    rows = connection.execute(
        "SELECT achievement_slug FROM user_achievements WHERE user_id = ? ORDER BY awarded_at",
        (user_id,),
    ).fetchall()
    return [row["achievement_slug"] for row in rows]


def user_achievements(connection: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = connection.execute(
        """
        SELECT a.slug, a.name, a.description, a.icon, ua.awarded_at
        FROM user_achievements ua
        JOIN achievements a ON a.slug = ua.achievement_slug
        WHERE ua.user_id = ?
        ORDER BY ua.awarded_at, a.slug
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def grant_achievement(
    connection: sqlite3.Connection,
    user_id: str,
    achievement_slug: str,
    awarded_by: str | None = None,
) -> bool:
    cursor = connection.execute(
        "INSERT OR IGNORE INTO user_achievements "
        "(user_id, achievement_slug, awarded_at, awarded_by) VALUES (?, ?, datetime('now'), ?)",
        (user_id, achievement_slug, awarded_by),
    )
    return cursor.rowcount > 0
