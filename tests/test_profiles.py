from tests.conftest import ASGITestClient


def test_registration_adds_sprout_badge_and_profile_can_be_updated(client: ASGITestClient):
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "profile_player", "password": "PlayerPass123!"},
    )
    assert response.status_code == 201
    payload = response.json()
    headers = {"Authorization": f"Bearer {payload['access_token']}"}
    assert "sprout_member" in payload["user"]["achievement_slugs"]

    profile = client.get("/api/v1/users/me/profile", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["achievements"][0]["slug"] == "sprout_member"

    updated = client.patch(
        "/api/v1/users/me/profile",
        headers=headers,
        json={
            "avatar_url": "https://cdn.example.test/profile.png",
            "signature": "Keep learning.",
            "direction": "Web",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["avatar_url"] == "https://cdn.example.test/profile.png"
    assert updated.json()["signature"] == "Keep learning."
    assert updated.json()["direction"] == "Web"


def test_admin_can_grant_core_badge_and_player_can_change_password(
    client: ASGITestClient,
    admin_headers: dict[str, str],
):
    registration = client.post(
        "/api/v1/auth/register",
        json={"username": "core_candidate", "password": "PlayerPass123!"},
    ).json()
    player_headers = {"Authorization": f"Bearer {registration['access_token']}"}
    user_id = registration["user"]["id"]

    granted = client.post(
        f"/api/v1/users/{user_id}/achievements/core_member",
        headers=admin_headers,
    )
    assert granted.status_code == 200
    assert granted.json()["slug"] == "core_member"

    profile = client.get(f"/api/v1/users/{user_id}/profile", headers=player_headers)
    assert {item["slug"] for item in profile.json()["achievements"]} == {
        "sprout_member",
        "core_member",
    }

    changed = client.post(
        "/api/v1/users/me/password",
        headers=player_headers,
        json={
            "current_password": "PlayerPass123!",
            "new_password": "NewPlayerPass123!",
        },
    )
    assert changed.status_code == 200
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "core_candidate", "password": "NewPlayerPass123!"},
    )
    assert login.status_code == 200
