from __future__ import annotations

from app.services.flags import (
    PLACEHOLDER,
    looks_like_flag,
    normalize_template,
    render_instance_flag,
)
from tests.conftest import auth_header


def test_normalize_template_keeps_fixed_and_marks_random_flags():
    assert normalize_template("SYC{fixed_value}") == "SYC{fixed_value}"
    assert normalize_template("SYC{<RANDOM>}") == "SYC{<RANDOM>}"
    assert normalize_template("SYCLOVER{<RANDOM>}") == "SYCLOVER{<RANDOM>}"
    assert normalize_template("plain-text-flag") == "SYC{<RANDOM>}"
    assert normalize_template("plain-text-flag", default_prefix="FLG") == "FLG{<RANDOM>}"


def test_render_instance_flag_is_unique_per_instance():
    first = render_instance_flag("SYCLOVER{<RANDOM>}")
    second = render_instance_flag("SYCLOVER{<RANDOM>}")

    assert first.startswith("SYCLOVER{") and first.endswith("}")
    assert first != second
    assert looks_like_flag(first) and looks_like_flag(second)
    assert render_instance_flag("SYC{fixed_value}") == "SYC{fixed_value}"
    assert looks_like_flag(render_instance_flag(None))
    assert looks_like_flag(render_instance_flag(normalize_template("plain-text-flag")))


def test_instance_receives_its_own_flag_and_submits_it(client, admin_headers, player_headers):
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
        "flag": "SYCLOVER{<RANDOM>}",
        "status": "draft",
    }
    created = client.post("/api/v1/challenges", headers=admin_headers, json=payload)
    assert created.status_code == 201
    challenge_id = created.json()["id"]
    assert created.json()["flag_template"] == "SYCLOVER{<RANDOM>}"

    published = client.patch(
        f"/api/v1/challenges/{challenge_id}", headers=admin_headers, json={"status": "published"}
    )
    assert published.status_code == 200
    anonymous = auth_header(client, "template_viewer", "TemplatePass123!")
    visible = client.get(f"/api/v1/challenges/{challenge_id}", headers=anonymous).json()
    assert visible["flag_template"] is None

    instance = client.post(f"/api/v1/instances/{challenge_id}", headers=player_headers)
    assert instance.status_code == 201
    instance_flag = instance.json()["instance_flag"]
    assert instance_flag and instance_flag.startswith("SYCLOVER{") and instance_flag != "SYCLOVER{<RANDOM>}"
    listed = client.get("/api/v1/instances", headers=player_headers).json()
    assert listed[0]["instance_flag"] == instance_flag
    admin_view = client.get("/api/v1/instances", headers=admin_headers).json()
    assert admin_view[0]["instance_flag"] is None

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
    assert correct.json() == {
        "correct": True,
        "awarded_points": 120,
        "message": "Correct flag",
    }

    other = auth_header(client, "flag_borrower", "FlagBorrow123!")
    borrowed = client.post(
        f"/api/v1/challenges/{challenge_id}/submit",
        headers=other,
        json={"flag": instance_flag},
    )
    assert borrowed.json()["correct"] is False


def test_placeholder_only_template_is_hidden_and_still_scored(client, admin_headers, player_headers):
    payload = {
        "title": "Opaque Template",
        "slug": "opaque-template",
        "description": "A flag without the PREFIX{...} convention still gets random values.",
        "category": "Misc",
        "mode": "ctf",
        "difficulty": "noob",
        "points": 60,
        "docker_image": "alpine:3.20",
        "internal_port": 9999,
        "flag": "plain-text-flag",
        "status": "published",
    }
    challenge_id = client.post("/api/v1/challenges", headers=admin_headers, json=payload).json()["id"]
    instance = client.post(f"/api/v1/instances/{challenge_id}", headers=player_headers).json()
    assert instance["instance_flag"].startswith("SYC{")

    visible = client.get(f"/api/v1/challenges/{challenge_id}", headers=player_headers).json()
    assert visible["flag_template"] is None

    correct = client.post(
        f"/api/v1/challenges/{challenge_id}/submit",
        headers=player_headers,
        json={"flag": instance["instance_flag"]},
    )
    assert correct.json()["correct"] is True
    assert PLACEHOLDER not in (visible["flag_template"] or "")
