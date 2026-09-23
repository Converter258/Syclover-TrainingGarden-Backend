import uuid

from app.core.database import Database


def test_existing_distinct_solves_receive_milestones_on_upgrade(client, player_headers, settings):
    database = Database(settings.database_path)
    user_id = client.get("/api/v1/users/me/profile", headers=player_headers).json()["id"]
    with database.connect() as connection:
        for index in range(10):
            challenge_id = str(uuid.uuid4())
            solved_at = f"2026-02-{index + 1:02d}T12:00:00+00:00"
            connection.execute(
                "INSERT INTO challenges (id, title, slug, description, category, mode, difficulty, "
                "points, flag_digest, status, created_at, updated_at) "
                "VALUES (?, ?, ?, '', 'Web', 'ctf', 'easy', 100, 'digest', 'published', ?, ?)",
                (challenge_id, f"Milestone {index}", f"milestone-{index}", solved_at, solved_at),
            )
            connection.execute(
                "INSERT INTO submissions (id, user_id, challenge_id, correct, awarded_points, created_at) "
                "VALUES (?, ?, ?, 1, 100, ?)",
                (str(uuid.uuid4()), user_id, challenge_id, solved_at),
            )

    database.initialize()
    profile = client.get("/api/v1/users/me/profile", headers=player_headers).json()
    awards = {item["slug"]: item["awarded_at"] for item in profile["achievements"]}
    assert awards["first_solve"] == "2026-02-01T12:00:00Z"
    assert awards["five_solves"] == "2026-02-05T12:00:00Z"
    assert awards["ten_solves"] == "2026-02-10T12:00:00Z"

    database.initialize()
    again = client.get("/api/v1/users/me/profile", headers=player_headers).json()
    assert {item["slug"]: item["awarded_at"] for item in again["achievements"]} == awards
