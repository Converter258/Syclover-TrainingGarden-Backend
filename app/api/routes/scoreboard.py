from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DatabaseDep
from app.schemas import ScoreEntry, ScoreboardResponse

router = APIRouter(prefix="/scoreboard", tags=["scoreboard"])


@router.get("", response_model=ScoreboardResponse)
async def get_scoreboard(
    _: CurrentUser,
    database: DatabaseDep,
    mode: str | None = Query(default=None, pattern="^(ctf|awdp)$"),
) -> ScoreboardResponse:
    with database.connect() as connection:
        stats = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM users WHERE role = 'player' AND is_active = 1) AS participants,
                (SELECT COUNT(*) FROM challenges WHERE status = 'published'
                    AND (? IS NULL OR mode = ?)) AS published_challenges,
                (SELECT COUNT(*) FROM submissions s JOIN challenges c ON c.id = s.challenge_id
                    WHERE s.correct = 1 AND s.awarded_points > 0
                    AND (? IS NULL OR c.mode = ?)) AS total_solves
            """,
            (mode, mode, mode, mode),
        ).fetchone()
        rows = connection.execute(
            """
            SELECT u.id AS user_id, u.username,
                   COALESCE(SUM(s.awarded_points), 0) AS score,
                   COUNT(CASE WHEN s.correct = 1 AND s.awarded_points > 0 THEN 1 END) AS solves,
                   MAX(CASE WHEN s.correct = 1 AND s.awarded_points > 0 THEN s.created_at END) AS last_solve_at
            FROM users u
            LEFT JOIN submissions s ON s.user_id = u.id AND s.challenge_id IN (
                SELECT id FROM challenges WHERE (? IS NULL OR mode = ?)
            )
            WHERE u.role = 'player' AND u.is_active = 1
            GROUP BY u.id, u.username
            ORDER BY score DESC, last_solve_at ASC, u.created_at ASC
            """,
            (mode, mode),
        ).fetchall()
    rankings = [
        ScoreEntry(rank=index, **dict(row)) for index, row in enumerate(rows, start=1)
    ]
    return ScoreboardResponse(**dict(stats), rankings=rankings)
