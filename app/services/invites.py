"""Registration invitation codes.

Registration is invitation-only: every new account must present a code that was
issued by an administrator. A code can be redeemed exactly once, which is
enforced by a conditional ``UPDATE`` inside the same transaction as the account
insert, so two concurrent registrations cannot share one code.
"""

from __future__ import annotations

import secrets
import sqlite3
import uuid
from datetime import UTC, datetime

INVITE_PREFIX = "SYC"
# Ambiguous glyphs (I, L, O, 0, 1) are excluded so codes survive being read aloud
# or copied by hand.
INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_GROUP_SIZE = 4
INVITE_GROUPS = 3
INVITE_BODY_LENGTH = INVITE_GROUP_SIZE * INVITE_GROUPS
MAX_CODES_PER_REQUEST = 50

INVITE_SELECT = """
SELECT i.id, i.code, i.note, i.created_at, i.used_at, i.created_by, i.used_by,
       creator.username AS created_by_username,
       consumer.username AS used_by_username
FROM invite_codes i
LEFT JOIN users creator ON creator.id = i.created_by
LEFT JOIN users consumer ON consumer.id = i.used_by
"""


def _format_invite_body(body: str) -> str:
    groups = [
        body[index : index + INVITE_GROUP_SIZE]
        for index in range(0, INVITE_BODY_LENGTH, INVITE_GROUP_SIZE)
    ]
    return "-".join((INVITE_PREFIX, *groups))


def canonical_invite_code(value: str | None) -> str:
    """Normalize typed input to the stored ``SYC-XXXX-XXXX-XXXX`` shape.

    Case, dashes and the ``SYC`` prefix are all optional so a member can paste a
    code straight out of chat. Anything that does not reduce to exactly one body
    length returns an empty string, which never matches a stored code.
    """
    raw = "".join(
        character
        for character in (value or "").upper()
        if character.isascii() and character.isalnum()
    )
    if len(raw) == INVITE_BODY_LENGTH + len(INVITE_PREFIX) and raw.startswith(INVITE_PREFIX):
        raw = raw[len(INVITE_PREFIX) :]
    if len(raw) != INVITE_BODY_LENGTH:
        return ""
    return _format_invite_body(raw)


def generate_invite_code() -> str:
    body = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_BODY_LENGTH))
    return _format_invite_body(body)


def find_invite_code(connection: sqlite3.Connection, code: str | None) -> sqlite3.Row | None:
    canonical = canonical_invite_code(code)
    if not canonical:
        return None
    return connection.execute(
        "SELECT id, code, note, created_by, used_by, created_at, used_at "
        "FROM invite_codes WHERE code = ?",
        (canonical,),
    ).fetchone()


def claim_invite_code(connection: sqlite3.Connection, invite_id: str, user_id: str) -> bool:
    """Consume a code for ``user_id``; ``False`` means somebody got there first.

    ``used_at`` rather than ``used_by`` records consumption, so releasing the
    account (which nulls the foreign key) can never make a code reusable.
    """
    cursor = connection.execute(
        "UPDATE invite_codes SET used_by = ?, used_at = ? WHERE id = ? AND used_at IS NULL",
        (user_id, datetime.now(UTC).isoformat(), invite_id),
    )
    return cursor.rowcount == 1


def create_invite_codes(
    connection: sqlite3.Connection,
    *,
    count: int,
    note: str | None,
    created_by: str,
) -> list[str]:
    now = datetime.now(UTC).isoformat()
    created: list[str] = []
    for _ in range(count):
        for _attempt in range(20):
            code = generate_invite_code()
            try:
                connection.execute(
                    "INSERT INTO invite_codes (id, code, note, created_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), code, note, created_by, now),
                )
            except sqlite3.IntegrityError:
                continue
            created.append(code)
            break
        else:  # pragma: no cover - needs 20 straight collisions in a 60-bit space
            raise RuntimeError("Unable to allocate a unique invitation code")
    return created


def revoke_invite_code(connection: sqlite3.Connection, invite_id: str) -> bool:
    """Delete a code while it is still unused; ``False`` means it was already redeemed.

    The "unused" condition is part of the ``DELETE`` rather than a preceding read:
    an administrator revoking a code at the same moment a member redeems it must
    not erase the registration record.
    """
    cursor = connection.execute(
        "DELETE FROM invite_codes WHERE id = ? AND used_at IS NULL", (invite_id,)
    )
    return cursor.rowcount == 1


def serialize_invite(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["status"] = "used" if data["used_at"] else "unused"
    return data
