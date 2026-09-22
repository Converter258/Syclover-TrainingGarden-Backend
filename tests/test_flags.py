from __future__ import annotations

from app.services.docker import FLAG_PATTERN
from app.services.flags import (
    LEGACY_PLACEHOLDER,
    RAND_TOKEN,
    dynamic_flag_for_template,
    looks_like_flag,
    normalize_template,
    render_instance_flag,
    template_wants_random,
)
from tests.conftest import auth_header


def test_normalize_template_keeps_the_administrator_text():
    assert normalize_template("SYC{fixed_value}") == "SYC{fixed_value}"
    assert normalize_template("SYC{RAND}") == "SYC{RAND}"
    # the legacy placeholder is upgraded rather than kept verbatim
    assert normalize_template("SYC{<RANDOM>}") == "SYC{RAND}"
    # malformed flags are rebuilt so a shared literal is never handed to every instance
    assert normalize_template("plain-text-flag") == "SYC{RAND}"
    assert normalize_template("plain-text-flag", default_prefix="FLG") == "FLG{RAND}"


def test_dynamic_switch_follows_the_admin_and_the_legacy_placeholder():
    # the switch is explicit: the RAND token alone must not randomise a literal flag
    assert dynamic_flag_for_template("SYC{RAND}", False) is False
    assert dynamic_flag_for_template("SYC{fixed}", True) is True
    assert dynamic_flag_for_template("SYC{fixed}", False) is False
    # the Alpha0.0.2 placeholder always implies randomisation
    assert dynamic_flag_for_template(f"SYC{LEGACY_PLACEHOLDER}", False) is True
    assert template_wants_random("SYC{RAND}") is True
    assert template_wants_random("SYC{fixed}") is False


def test_literal_flag_containing_rand_is_left_alone():
    """The reported bug: SYC{..._RAND} must not be mangled or randomised implicitly."""
    flag = "SYC{y0u_4r3_m4tH_G3nius_RAND}"
    template = normalize_template(flag)
    assert template == flag
    assert dynamic_flag_for_template(template, False) is False
    assert render_instance_flag(template, dynamic=False) == flag
    rendered = render_instance_flag(template, dynamic=True)
    assert rendered.startswith("SYC{y0u_4r3_m4tH_G3nius_") and rendered.endswith("}")
    assert rendered != flag and len(rendered) > len(flag)
    assert FLAG_PATTERN.fullmatch(rendered)


def test_legacy_placeholder_is_upgraded_instead_of_dropped():
    assert normalize_template("SYC{<RANDOM>}") == "SYC{RAND}"
    assert normalize_template("<RANDOM>") == "SYC{RAND}"
    assert render_instance_flag("SYC{RAND}", dynamic=True).startswith("SYC{")


def test_every_supported_flag_shape_renders_a_valid_instance_flag():
    for flag, dynamic in (
        ("SYC{y0u_4r3_m4tH_G3nius_RAND}", True),
        ("SYC{y0u_4r3_m4tH_G3nius_RAND}", False),
        ("SYC{<RANDOM>}", True),
        ("SYC{fixed_value}", False),
        ("SYC{fixed_value}", True),
        ("plain-text-flag", True),
    ):
        template = normalize_template(flag)
        enabled = dynamic_flag_for_template(template, dynamic)
        assert FLAG_PATTERN.fullmatch(render_instance_flag(template, dynamic=enabled)), (flag, dynamic)


def test_render_instance_flag_randomises_only_when_enabled():
    first = render_instance_flag("SYC{RAND}", dynamic=True)
    second = render_instance_flag("SYC{RAND}", dynamic=True)
    assert first.startswith("SYC{") and first.endswith("}")
    assert RAND_TOKEN not in first
    assert first != second
    assert looks_like_flag(first) and looks_like_flag(second)

    # a static flag stays literally the same for every instance
    assert render_instance_flag("SYC{fixed_value}", dynamic=False) == "SYC{fixed_value}"
    # the switch alone is enough, even without an explicit token
    appended = render_instance_flag("SYC{base}", dynamic=True)
    assert appended.startswith("SYC{base") and appended.endswith("}")
    assert render_instance_flag(None, dynamic=True) != render_instance_flag(None, dynamic=True)
    assert looks_like_flag(render_instance_flag(normalize_template("plain-text-flag"), dynamic=True))


def test_players_never_see_the_instance_flag_only_admins_do(client, admin_headers, player_headers):
    challenge = next(
        item
        for item in client.get("/api/v1/challenges?mode=ctf", headers=player_headers).json()
        if item["slug"] == "welcome-header"
    )
    started = client.post(f"/api/v1/instances/{challenge['id']}", headers=player_headers)
    assert started.status_code == 201
    instance_id = started.json()["id"]
    assert started.json()["instance_flag"] is None

    player_view = client.get(f"/api/v1/instances/{instance_id}", headers=player_headers).json()
    assert player_view["instance_flag"] is None
    assert player_view["connect_command"]

    admin_view = client.get(f"/api/v1/instances/{instance_id}", headers=admin_headers).json()
    admin_flag = admin_view["instance_flag"]
    assert admin_flag

    submitted = client.post(
        f"/api/v1/challenges/{challenge['id']}/submit",
        headers=player_headers,
        json={"flag": admin_flag},
    )
    assert submitted.json()["correct"] is True


def test_instance_flag_follows_its_own_instance(client, admin_headers, player_headers):
    payload = {
        "title": "Randomised Box",
        "slug": "randomised-box",
        "description": "Each instance carries its own random flag.",
        "category": "Pwn",
        "mode": "ctf",
        "difficulty": "easy",
        "points": 120,
        "docker_image": "alpine:3.20",
        "internal_port": 9999,
        "flag": "SYCLOVER{RAND}",
        "dynamic_flag": True,
        "status": "draft",
    }
    created = client.post("/api/v1/challenges", headers=admin_headers, json=payload)
    assert created.status_code == 201
    body = created.json()
    challenge_id = body["id"]
    assert body["flag_template"] == "SYCLOVER{RAND}"
    assert body["dynamic_flag"] is True

    published = client.patch(
        f"/api/v1/challenges/{challenge_id}", headers=admin_headers, json={"status": "published"}
    )
    assert published.status_code == 200
    viewer = auth_header(client, "template_viewer", "TemplatePass123!")
    assert client.get(f"/api/v1/challenges/{challenge_id}", headers=viewer).json()["flag_template"] is None

    instance = client.post(f"/api/v1/instances/{challenge_id}", headers=player_headers)
    assert instance.status_code == 201
    instance_id = instance.json()["id"]
    instance_flag = client.get(f"/api/v1/instances/{instance_id}", headers=admin_headers).json()["instance_flag"]
    assert instance_flag.startswith("SYCLOVER{") and RAND_TOKEN not in instance_flag

    wrong = client.post(
        f"/api/v1/challenges/{challenge_id}/submit",
        headers=player_headers,
        json={"flag": "SYC{not_the_instance_flag}"},
    )
    assert wrong.json()["correct"] is False
    correct = client.post(
        f"/api/v1/challenges/{challenge_id}/submit",
        headers=player_headers,
        json={"flag": instance_flag},
    )
    assert correct.json() == {"correct": True, "awarded_points": 120, "message": "Correct flag"}

    other = auth_header(client, "flag_borrower", "FlagBorrow123!")
    assert client.post(
        f"/api/v1/challenges/{challenge_id}/submit",
        headers=other,
        json={"flag": instance_flag},
    ).json()["correct"] is False


def test_static_flag_challenge_keeps_one_shared_answer(client, admin_headers, player_headers):
    payload = {
        "title": "Static Answer",
        "slug": "static-answer",
        "description": "A challenge that deliberately shares one literal flag.",
        "category": "Misc",
        "mode": "ctf",
        "difficulty": "noob",
        "points": 60,
        "docker_image": "alpine:3.20",
        "internal_port": 9999,
        "flag": "SYC{shared_secret}",
        "dynamic_flag": False,
        "status": "published",
    }
    created = client.post("/api/v1/challenges", headers=admin_headers, json=payload).json()
    assert created["dynamic_flag"] is False
    assert created["flag_template"] == "SYC{shared_secret}"

    instance = client.post(f"/api/v1/instances/{created['id']}", headers=player_headers).json()
    admin_flag = client.get(f"/api/v1/instances/{instance['id']}", headers=admin_headers).json()["instance_flag"]
    assert admin_flag == "SYC{shared_secret}"

    accepted = client.post(
        f"/api/v1/challenges/{created['id']}/submit",
        headers=player_headers,
        json={"flag": "SYC{shared_secret}"},
    )
    assert accepted.json()["correct"] is True


def test_dynamic_switch_can_be_toggled_after_creation(client, admin_headers):
    payload = {
        "title": "Toggle Me",
        "slug": "toggle-me",
        "description": "The dynamic switch can be flipped without changing the flag.",
        "category": "Misc",
        "mode": "ctf",
        "difficulty": "noob",
        "points": 60,
        "docker_image": "alpine:3.20",
        "internal_port": 9999,
        "flag": "SYC{toggled}",
        "status": "published",
    }
    created = client.post("/api/v1/challenges", headers=admin_headers, json=payload).json()
    assert created["dynamic_flag"] is False

    enabled = client.patch(
        f"/api/v1/challenges/{created['id']}", headers=admin_headers, json={"dynamic_flag": True}
    )
    assert enabled.status_code == 200
    assert enabled.json()["dynamic_flag"] is True

    disabled = client.patch(
        f"/api/v1/challenges/{created['id']}", headers=admin_headers, json={"dynamic_flag": False}
    )
    assert disabled.json()["dynamic_flag"] is False


def test_legacy_placeholder_flag_is_migrated_to_the_dynamic_switch(client, admin_headers):
    created = client.post(
        "/api/v1/challenges",
        headers=admin_headers,
        json={
            "title": "Legacy Token",
            "slug": "legacy-token",
            "description": "Created with the previous placeholder syntax.",
            "category": "Misc",
            "mode": "ctf",
            "difficulty": "noob",
            "points": 60,
            "docker_image": "alpine:3.20",
            "internal_port": 9999,
            "flag": "SYC{<RANDOM>}",
            "status": "published",
        },
    ).json()
    assert created["dynamic_flag"] is True
    assert created["flag_template"] == "SYC{RAND}"
