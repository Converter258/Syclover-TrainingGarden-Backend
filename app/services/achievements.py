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
        SELECT a.slug, a.name, a.description, a.acquisition, a.icon, ua.awarded_at
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
    awarded_at: str | None = None,
) -> bool:
    if achievement_slug == "core_member":
        connection.execute(
            "DELETE FROM user_achievements WHERE user_id = ? AND achievement_slug = 'sprout_member'",
            (user_id,),
        )
    cursor = connection.execute(
        "INSERT OR IGNORE INTO user_achievements "
        "(user_id, achievement_slug, awarded_at, awarded_by) "
        "VALUES (?, ?, COALESCE(?, datetime('now')), ?)",
        (user_id, achievement_slug, awarded_at, awarded_by),
    )
    return cursor.rowcount > 0


def sync_progress_achievements(connection: sqlite3.Connection, user_id: str) -> None:
    """Grant solve milestones once, using the event time for existing records too."""
    solves = connection.execute(
        "SELECT MIN(created_at) AS created_at FROM submissions WHERE user_id = ? AND correct = 1 "
        "AND awarded_points > 0 GROUP BY challenge_id ORDER BY created_at LIMIT 10",
        (user_id,),
    ).fetchall()
    for threshold, slug in ((1, "first_solve"), (5, "five_solves"), (10, "ten_solves")):
        if len(solves) >= threshold:
            grant_achievement(connection, user_id, slug, awarded_at=solves[threshold - 1]["created_at"])
    defense = connection.execute(
        "SELECT created_at FROM defense_solves WHERE user_id = ? ORDER BY created_at, id LIMIT 1",
        (user_id,),
    ).fetchone()
    if defense:
        grant_achievement(connection, user_id, "first_defense", awarded_at=defense["created_at"])
    first_try = connection.execute(
        "SELECT s.created_at FROM submissions s WHERE s.user_id = ? AND s.correct = 1 "
        "AND s.awarded_points > 0 AND NOT EXISTS ("
        "SELECT 1 FROM submissions previous WHERE previous.user_id = s.user_id "
        "AND previous.challenge_id = s.challenge_id AND previous.rowid < s.rowid) "
        "ORDER BY s.created_at, s.rowid LIMIT 1",
        (user_id,),
    ).fetchone()
    if first_try:
        grant_achievement(connection, user_id, "first_try", awarded_at=first_try["created_at"])
    comeback = connection.execute(
        "SELECT s.created_at FROM submissions s WHERE s.user_id = ? AND s.correct = 1 "
        "AND s.awarded_points > 0 AND ("
        "SELECT COUNT(*) FROM submissions previous WHERE previous.user_id = s.user_id "
        "AND previous.challenge_id = s.challenge_id AND previous.correct = 0 "
        "AND previous.rowid < s.rowid) >= 3 "
        "ORDER BY s.created_at, s.rowid LIMIT 1",
        (user_id,),
    ).fetchone()
    if comeback:
        grant_achievement(connection, user_id, "comeback", awarded_at=comeback["created_at"])
    categories = connection.execute(
        "SELECT c.category, MIN(s.created_at) AS solved_at FROM submissions s "
        "JOIN challenges c ON c.id = s.challenge_id WHERE s.user_id = ? "
        "AND s.correct = 1 AND s.awarded_points > 0 GROUP BY c.category "
        "ORDER BY solved_at LIMIT 3",
        (user_id,),
    ).fetchall()
    if len(categories) == 3:
        grant_achievement(connection, user_id, "versatile", awarded_at=categories[2]["solved_at"])


def maybe_grant_peak_geek_2025(connection: sqlite3.Connection, user_id: str) -> bool:
    """Award Peak Geek 2025 after every marked CTF/AWDP challenge is completed."""
    counts = connection.execute(
        """
        WITH event_challenges AS (
            SELECT c.id
            FROM challenges c
            WHERE c.status != 'archived' AND (
                (lower(c.title || ' ' || c.slug || ' ' || c.description) LIKE '%2025%'
                 AND (lower(c.title || ' ' || c.slug || ' ' || c.description) LIKE '%geek%'
                      OR c.title || ' ' || c.slug || ' ' || c.description LIKE '%极客%'))
                OR EXISTS (
                    SELECT 1 FROM challenge_tags ct JOIN tags t ON t.id = ct.tag_id
                    WHERE ct.challenge_id = c.id
                      AND lower(t.name) IN ('peak-geek-2025', 'geek-2025', '2025-geek', '极客大挑战2025')
                )
            )
        )
        SELECT
            (SELECT COUNT(*) FROM event_challenges) AS total,
            (SELECT COUNT(*) FROM event_challenges c WHERE
                EXISTS (
                    SELECT 1 FROM submissions s
                    WHERE s.user_id = ? AND s.challenge_id = c.id AND s.correct = 1
                )
                OR EXISTS (
                    SELECT 1 FROM defense_solves d
                    WHERE d.user_id = ? AND d.challenge_id = c.id
                )
            ) AS completed
        """,
        (user_id, user_id),
    ).fetchone()
    if not counts or not counts["total"] or counts["completed"] < counts["total"]:
        return False
    return grant_achievement(connection, user_id, "peak_geek_2025")
