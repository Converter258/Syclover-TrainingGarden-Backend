"""Challenge tags.

Topic tags (``web``, ``pwn``, ...) live in the ``tags`` table and are editable by
administrators. The dynamic/static state of a challenge is derived from whether it
ships a Docker environment, so it can never drift away from reality.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

DYNAMIC_TAG = "dynamic"
STATIC_TAG = "static"
STATE_TAGS = {
    DYNAMIC_TAG: "动态题目：启动独立 Docker 实例",
    STATIC_TAG: "静态题目：仅附件与描述",
}


def slugify_tag(value: str) -> str:
    """Normalize an administrator supplied tag name."""
    cleaned = "".join(
        character if character.isalnum() or character in "-_." else "-" for character in value.strip()
    )
    return cleaned.strip("-").lower()


def state_tag_for(docker_image: str | None, internal_port: int | None) -> str:
    return DYNAMIC_TAG if docker_image and internal_port else STATIC_TAG


def tags_for_challenge(connection, challenge_id: str, docker_image: str | None, internal_port: int | None) -> list[str]:
    """Topic tags first (by sort order), then the derived state tag."""
    rows = connection.execute(
        """
        SELECT t.name FROM challenge_tags ct JOIN tags t ON t.id = ct.tag_id
        WHERE ct.challenge_id = ? ORDER BY t.sort_order, t.name
        """,
        (challenge_id,),
    ).fetchall()
    return [row["name"] for row in rows] + [state_tag_for(docker_image, internal_port)]


def set_challenge_tags(connection, challenge_id: str, names: list[str]) -> list[str]:
    """Replace a challenge's topic tags, creating unknown tags on demand.

    State tags are derived, so they are ignored as input and re-appended on output.
    """
    now = datetime.now(UTC).isoformat()
    requested: list[str] = []
    for raw in names:
        name = slugify_tag(raw)
        if not name or name in STATE_TAGS or name in requested:
            continue
        requested.append(name)
    connection.execute("DELETE FROM challenge_tags WHERE challenge_id = ?", (challenge_id,))
    for name in requested:
        row = connection.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row:
            tag_id = row["id"]
        else:
            tag_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO tags (id, name, kind, description, sort_order, created_at) "
                "VALUES (?, ?, 'topic', NULL, 100, ?)",
                (tag_id, name, now),
            )
        connection.execute(
            "INSERT OR IGNORE INTO challenge_tags (challenge_id, tag_id, created_at) VALUES (?, ?, ?)",
            (challenge_id, tag_id, now),
        )
    challenge = connection.execute(
        "SELECT docker_image, internal_port FROM challenges WHERE id = ?", (challenge_id,)
    ).fetchone()
    if not challenge:
        return []
    return tags_for_challenge(
        connection, challenge_id, challenge["docker_image"], challenge["internal_port"]
    )
