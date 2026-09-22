"""Invitation-only registration (Alpha0.0.6-hotfix.1).

Covers the three contract points: registration needs a code, a code is good for
exactly one account, and only administrators may issue codes.
"""

from app.core.database import Database
from app.services.invites import (
    canonical_invite_code,
    claim_invite_code,
    create_invite_codes,
    generate_invite_code,
    revoke_invite_code,
)
from tests.conftest import ASGITestClient, auth_header, issue_invite_codes


def _register(client: ASGITestClient, username: str, code: str | None, password: str = "PlayerPass123!"):
    payload: dict[str, str] = {"username": username, "password": password}
    if code is not None:
        payload["invite_code"] = code
    return client.post("/api/v1/auth/register", json=payload)


def test_registration_without_an_invitation_code_is_rejected(client: ASGITestClient):
    response = _register(client, "no_code_player", None)
    assert response.status_code == 422
    assert client.post(
        "/api/v1/auth/login", json={"username": "no_code_player", "password": "PlayerPass123!"}
    ).status_code == 401


def test_registration_rejects_unknown_malformed_and_used_codes(client: ASGITestClient):
    assert _register(client, "unknown_code", "SYC-AAAA-BBBB-CCCC").status_code == 400
    assert _register(client, "malformed_code", "not-a-real-code-entropy").status_code == 400
    assert _register(client, "blank_code", "        ").status_code == 400
    assert _register(client, "empty_code", "").status_code == 422

    code = issue_invite_codes(client)[0]
    assert _register(client, "first_owner", code).status_code == 201
    reused = _register(client, "second_owner", code)
    assert reused.status_code == 409
    assert reused.json()["detail"] == "Invitation code has already been used"


def test_invitation_code_is_accepted_regardless_of_typed_format(client: ASGITestClient):
    code = issue_invite_codes(client)[0]
    assert _register(client, "sloppy_typist", f"  {code.replace('-', '').lower()}  ").status_code == 201
    assert _register(client, "second_typist", code).status_code == 409


def test_a_failed_registration_does_not_burn_the_code(client: ASGITestClient):
    spent = issue_invite_codes(client)[0]
    assert _register(client, "taken_name", spent).status_code == 201

    rejected = issue_invite_codes(client)[0]
    duplicate = _register(client, "TAKEN_NAME", rejected)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Username is already registered"

    # The rejected attempt left its own code untouched, so it still works.
    assert _register(client, "recovering_player", rejected).status_code == 201
    # The spent code stays spent.
    assert _register(client, "third_owner", spent).status_code == 409


def test_second_registration_with_a_fresh_code_still_works(client: ASGITestClient):
    first, second = issue_invite_codes(client, count=2)
    assert _register(client, "member_one", first).status_code == 201
    assert _register(client, "member_two", second).status_code == 201


def test_only_administrators_can_issue_list_and_revoke_codes(
    client: ASGITestClient, admin_headers: dict[str, str]
):
    player_headers = auth_header(client, "curious_player")
    assert client.post("/api/v1/invites", json={"count": 1}, headers=player_headers).status_code == 403
    assert client.get("/api/v1/invites", headers=player_headers).status_code == 403

    issued = client.post("/api/v1/invites", json={"count": 1}, headers=admin_headers).json()
    assert client.delete(
        f"/api/v1/invites/{issued[0]['id']}", headers=player_headers
    ).status_code == 403

    assert client.post("/api/v1/invites", json={"count": 1}).status_code == 401
    assert client.get("/api/v1/invites").status_code == 401
    assert client.get(
        "/api/v1/invites", headers={"Authorization": "Bearer not-a-token"}
    ).status_code == 401


def test_batch_size_must_stay_within_limits(client: ASGITestClient, admin_headers: dict[str, str]):
    assert client.post("/api/v1/invites", json={"count": 0}, headers=admin_headers).status_code == 422
    assert client.post("/api/v1/invites", json={"count": 51}, headers=admin_headers).status_code == 422
    assert client.post(
        "/api/v1/invites", json={"count": 50}, headers=admin_headers
    ).status_code == 201


def test_administrator_issues_a_batch_and_tracks_usage(
    client: ASGITestClient, admin_headers: dict[str, str]
):
    created = client.post(
        "/api/v1/invites", json={"count": 3, "note": "2026 秋招"}, headers=admin_headers
    )
    assert created.status_code == 201
    batch = created.json()
    codes = [item["code"] for item in batch]
    assert len(set(codes)) == 3
    assert all(item["status"] == "unused" for item in batch)
    assert all(item["note"] == "2026 秋招" for item in batch)
    assert all(item["created_by_username"] == "Syclover" for item in batch)
    assert all(item["used_at"] is None for item in batch)

    assert _register(client, "batch_member", codes[0]).status_code == 201

    unused = client.get("/api/v1/invites?state=unused", headers=admin_headers).json()
    used = client.get("/api/v1/invites?state=used", headers=admin_headers).json()
    assert {item["code"] for item in unused} == set(codes[1:])
    assert [item["code"] for item in used] == [codes[0]]
    assert used[0]["used_by_username"] == "batch_member"
    assert used[0]["used_at"] is not None

    everything = client.get("/api/v1/invites", headers=admin_headers).json()
    assert len(everything) == 3


def test_unused_code_can_be_revoked_but_redeemed_codes_are_kept(
    client: ASGITestClient, admin_headers: dict[str, str]
):
    revoked, redeemed = client.post(
        "/api/v1/invites", json={"count": 2}, headers=admin_headers
    ).json()
    assert _register(client, "audited_member", redeemed["code"]).status_code == 201

    assert client.delete(
        f"/api/v1/invites/{revoked['id']}", headers=admin_headers
    ).status_code == 200
    assert _register(client, "late_member", revoked["code"]).status_code == 400

    blocked = client.delete(f"/api/v1/invites/{redeemed['id']}", headers=admin_headers)
    assert blocked.status_code == 409
    assert client.delete("/api/v1/invites/does-not-exist", headers=admin_headers).status_code == 404


def test_a_redeemed_code_survives_deletion_of_the_account(
    client: ASGITestClient, admin_headers: dict[str, str]
):
    """Deleting the member must not recycle their code back into circulation."""
    code = issue_invite_codes(client, headers=admin_headers)[0]
    registration = _register(client, "temporary_member", code).json()
    member_id = registration["user"]["id"]

    assert client.delete(f"/api/v1/users/{member_id}", headers=admin_headers).status_code == 200
    assert _register(client, "recycled_member", code).status_code == 409

    surviving = client.get("/api/v1/invites?state=used", headers=admin_headers).json()
    assert [item["code"] for item in surviving] == [code]
    assert surviving[0]["used_by"] is None
    assert surviving[0]["status"] == "used"


def test_codes_outlive_the_administrator_who_issued_them(
    client: ASGITestClient, admin_headers: dict[str, str]
):
    """A departing content admin must not take their codes (or their usage) with them."""
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "username": "issuing_admin",
            "password": "IssuingPass123!",
            "invite_code": issue_invite_codes(client, headers=admin_headers)[0],
        },
    ).json()
    issuer_id = registration["user"]["id"]
    assert client.patch(
        f"/api/v1/users/{issuer_id}", json={"role": "admin"}, headers=admin_headers
    ).status_code == 200
    issuer_headers = {
        "Authorization": f"Bearer {client.post('/api/v1/auth/login', json={'username': 'issuing_admin', 'password': 'IssuingPass123!'}).json()['access_token']}"
    }

    issued = client.post("/api/v1/invites", json={"count": 2}, headers=issuer_headers).json()
    assert all(item["created_by_username"] == "issuing_admin" for item in issued)

    assert client.delete(f"/api/v1/users/{issuer_id}", headers=admin_headers).status_code == 200

    listed = client.get("/api/v1/invites", headers=admin_headers).json()
    codes = {item["code"] for item in issued}
    orphans = [item for item in listed if item["code"] in codes]
    assert len(orphans) == 2
    assert all(item["created_by"] is None and item["created_by_username"] is None for item in orphans)
    # Inherited codes stay valid for onboarding after the issuer is gone.
    assert _register(client, "onboarded_member", issued[0]["code"]).status_code == 201


def test_code_generation_is_unambiguous_and_canonical():
    codes = {generate_invite_code() for _ in range(200)}
    assert len(codes) == 200
    for code in codes:
        assert code.startswith("SYC-")
        assert len(code) == len("SYC-XXXX-XXXX-XXXX")
        assert not set(code[4:].replace("-", "")) & set("ILO01")
        assert canonical_invite_code(code) == code

    assert canonical_invite_code("syc abcd efgh jklm") == "SYC-ABCD-EFGH-JKLM"
    assert canonical_invite_code("ABCD-EFGH-JKLM") == "SYC-ABCD-EFGH-JKLM"
    assert canonical_invite_code("") == ""
    assert canonical_invite_code(None) == ""
    assert canonical_invite_code("SYC-ABCD") == ""
    assert canonical_invite_code("邀请码") == ""
    # A body that itself starts with the prefix must not lose its first group.
    assert canonical_invite_code("SYC-SYCX-ABCD-EFGH") == "SYC-SYCX-ABCD-EFGH"
    assert canonical_invite_code("SYCX-ABCD-EFGH") == "SYC-SYCX-ABCD-EFGH"


def test_claim_is_atomic_so_a_code_cannot_fund_two_accounts(settings):
    """The conditional UPDATE is the single-use guarantee, not the earlier lookup."""
    database = Database(settings.database_path)
    database.initialize()
    with database.connect() as connection:
        connection.executemany(
            "INSERT INTO users (id, username, password_hash, role, is_active, created_at) "
            "VALUES (?, ?, 'hash', 'admin', 1, '2026-01-01T00:00:00+00:00')",
            [("creator", "creator"), ("first", "first"), ("second", "second")],
        )
        code = create_invite_codes(connection, count=1, note=None, created_by="creator")[0]
        invite_id = connection.execute(
            "SELECT id FROM invite_codes WHERE code = ?", (code,)
        ).fetchone()["id"]
        assert claim_invite_code(connection, invite_id, "first") is True
        assert claim_invite_code(connection, invite_id, "second") is False
        row = connection.execute(
            "SELECT used_by FROM invite_codes WHERE id = ?", (invite_id,)
        ).fetchone()
    assert row["used_by"] == "first"


def test_revoking_cannot_erase_a_code_that_was_just_redeemed(settings):
    """Revocation is conditional in the DELETE, not in a preceding read.

    An administrator looking at a stale "unused" list must not be able to delete
    a code that a member redeemed in the meantime: the registration audit record
    has to survive.
    """
    database = Database(settings.database_path)
    database.initialize()
    with database.connect() as connection:
        connection.executemany(
            "INSERT INTO users (id, username, password_hash, role, is_active, created_at) "
            "VALUES (?, ?, 'hash', 'admin', 1, '2026-01-01T00:00:00+00:00')",
            [("creator", "creator"), ("member", "member")],
        )
        code = create_invite_codes(connection, count=1, note=None, created_by="creator")[0]
        invite_id = connection.execute(
            "SELECT id FROM invite_codes WHERE code = ?", (code,)
        ).fetchone()["id"]
        # The administrator's list read the code as unused at this point.
        assert connection.execute(
            "SELECT used_at FROM invite_codes WHERE id = ?", (invite_id,)
        ).fetchone()["used_at"] is None
        assert claim_invite_code(connection, invite_id, "member") is True

    with database.connect() as connection:
        assert revoke_invite_code(connection, invite_id) is False
        assert revoke_invite_code(connection, "no-such-id") is False
        survivor = connection.execute(
            "SELECT used_by, used_at FROM invite_codes WHERE id = ?", (invite_id,)
        ).fetchone()
    assert survivor["used_by"] == "member"
    assert survivor["used_at"] is not None

    with database.connect() as connection:
        spare = create_invite_codes(connection, count=1, note=None, created_by="creator")[0]
        spare_id = connection.execute(
            "SELECT id FROM invite_codes WHERE code = ?", (spare,)
        ).fetchone()["id"]
        assert revoke_invite_code(connection, spare_id) is True
        assert connection.execute(
            "SELECT COUNT(*) FROM invite_codes WHERE id = ?", (spare_id,)
        ).fetchone()[0] == 0
