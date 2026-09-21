from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.api.deps import AdminUser, CurrentUser, DatabaseDep, DockerDep, SettingsDep
from app.core.security import digest_flag
from app.schemas import (
    AssetPublic,
    ChallengeCreate,
    ChallengePublic,
    ChallengeUpdate,
    Message,
    SubmissionRequest,
    SubmissionResult,
    category_is_valid,
)
from app.services.assets import safe_filename, store_bytes
from app.services.docker import ContainerError

router = APIRouter(prefix="/challenges", tags=["challenges"])


def _asset(row) -> AssetPublic:
    data = dict(row)
    data["download_url"] = f"/api/v1/challenges/assets/{data['id']}/download"
    return AssetPublic.model_validate(data)


def _challenge(row, solved: bool = False, attachments: list | None = None) -> ChallengePublic:
    return ChallengePublic.model_validate(
        {**dict(row), "solved": solved, "attachments": attachments or []}
    )


@router.get("", response_model=list[ChallengePublic])
async def list_challenges(
    user: CurrentUser,
    database: DatabaseDep,
    mode: str | None = Query(default=None, pattern="^(ctf|awdp)$"),
) -> list[ChallengePublic]:
    conditions = [] if user["role"] == "admin" else ["c.status = 'published'"]
    values: list[str] = []
    if mode:
        conditions.append("c.mode = ?")
        values.append(mode)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with database.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT c.*,
                   EXISTS(
                       SELECT 1 FROM submissions s
                       WHERE s.challenge_id = c.id AND s.user_id = ? AND s.correct = 1
                   ) AS solved,
                   EXISTS(
                       SELECT 1 FROM assets a WHERE a.challenge_id = c.id
                       AND a.kind = 'check_script' AND a.validation_status = 'valid'
                   ) AS check_script_configured,
                   EXISTS(
                       SELECT 1 FROM assets a WHERE a.challenge_id = c.id
                       AND a.kind = 'fix_script' AND a.validation_status = 'valid'
                   ) AS fix_script_configured
            FROM challenges c {where}
            ORDER BY CASE c.difficulty
                WHEN 'noob' THEN 1 WHEN 'easy' THEN 2 WHEN 'normal' THEN 3
                WHEN 'hard' THEN 4 ELSE 5 END, c.title
            """,
            (user["id"], *values),
        ).fetchall()
    return [_challenge(row, bool(row["solved"])) for row in rows]


@router.get("/{challenge_id}", response_model=ChallengePublic)
async def get_challenge(challenge_id: str, user: CurrentUser, database: DatabaseDep) -> ChallengePublic:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT c.*,
                   EXISTS(SELECT 1 FROM assets a WHERE a.challenge_id = c.id
                          AND a.kind = 'check_script' AND a.validation_status = 'valid')
                       AS check_script_configured,
                   EXISTS(SELECT 1 FROM assets a WHERE a.challenge_id = c.id
                          AND a.kind = 'fix_script' AND a.validation_status = 'valid')
                       AS fix_script_configured
            FROM challenges c WHERE c.id = ?
            """,
            (challenge_id,),
        ).fetchone()
        if not row or (row["status"] != "published" and user["role"] != "admin"):
            raise HTTPException(status_code=404, detail="Challenge not found")
        solved = connection.execute(
            "SELECT 1 FROM submissions WHERE challenge_id = ? AND user_id = ? AND correct = 1",
            (challenge_id, user["id"]),
        ).fetchone()
        assets = connection.execute(
            "SELECT * FROM assets WHERE challenge_id = ? AND kind = 'attachment' ORDER BY created_at",
            (challenge_id,),
        ).fetchall()
    return _challenge(row, bool(solved), [_asset(asset) for asset in assets])


@router.post("/{challenge_id}/submit", response_model=SubmissionResult)
async def submit_flag(
    challenge_id: str,
    payload: SubmissionRequest,
    user: CurrentUser,
    database: DatabaseDep,
    settings: SettingsDep,
) -> SubmissionResult:
    with database.connect() as connection:
        challenge = connection.execute(
            "SELECT id, points, flag_digest, status FROM challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
        if not challenge or (challenge["status"] != "published" and user["role"] != "admin"):
            raise HTTPException(status_code=404, detail="Challenge not found")
        correct = digest_flag(payload.flag, settings.secret_key) == challenge["flag_digest"]
        previous = connection.execute(
            "SELECT 1 FROM submissions WHERE user_id = ? AND challenge_id = ? AND correct = 1",
            (user["id"], challenge_id),
        ).fetchone()
        awarded = challenge["points"] if correct and not previous else 0
        connection.execute(
            "INSERT INTO submissions (id, user_id, challenge_id, correct, awarded_points, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                user["id"],
                challenge_id,
                int(correct),
                awarded,
                datetime.now(UTC).isoformat(),
            ),
        )
    if not correct:
        return SubmissionResult(correct=False, awarded_points=0, message="Flag is incorrect")
    if awarded == 0:
        return SubmissionResult(correct=True, awarded_points=0, message="Challenge was already solved")
    return SubmissionResult(correct=True, awarded_points=awarded, message="Correct flag")


@router.post("", response_model=ChallengePublic, status_code=status.HTTP_201_CREATED)
async def create_challenge(
    payload: ChallengeCreate, _: AdminUser, database: DatabaseDep, settings: SettingsDep
) -> ChallengePublic:
    if payload.mode == "awdp" and payload.status == "published":
        raise HTTPException(
            status_code=422,
            detail="Create AWDP challenges offline, upload Check and Fix scripts, then publish them",
        )
    challenge_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    data = payload.model_dump(exclude={"flag"})
    try:
        with database.connect() as connection:
            connection.execute(
                """
                INSERT INTO challenges (
                    id, title, slug, description, category, mode, difficulty, points,
                    docker_image, internal_port, flag_digest, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    challenge_id,
                    data["title"],
                    data["slug"],
                    data["description"],
                    data["category"],
                    data["mode"],
                    data["difficulty"],
                    data["points"],
                    data["docker_image"],
                    data["internal_port"],
                    digest_flag(payload.flag, settings.secret_key),
                    data["status"],
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT c.*,
                       EXISTS(SELECT 1 FROM assets a WHERE a.challenge_id = c.id
                              AND a.kind = 'check_script' AND a.validation_status = 'valid')
                           AS check_script_configured,
                       EXISTS(SELECT 1 FROM assets a WHERE a.challenge_id = c.id
                              AND a.kind = 'fix_script' AND a.validation_status = 'valid')
                           AS fix_script_configured
                FROM challenges c WHERE c.id = ?
                """,
                (challenge_id,),
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Challenge slug already exists") from exc
    return _challenge(row)


@router.patch("/{challenge_id}", response_model=ChallengePublic)
async def update_challenge(
    challenge_id: str,
    payload: ChallengeUpdate,
    _: AdminUser,
    database: DatabaseDep,
    settings: SettingsDep,
) -> ChallengePublic:
    fields = payload.model_dump(exclude_unset=True)
    flag = fields.pop("flag", None)
    if flag:
        fields["flag_digest"] = digest_flag(flag, settings.secret_key)
    try:
        with database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM challenges WHERE id = ?", (challenge_id,)
            ).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Challenge not found")
            resulting_mode = fields.get("mode", existing["mode"])
            resulting_category = fields.get("category", existing["category"])
            if not category_is_valid(resulting_mode, resulting_category):
                raise HTTPException(
                    status_code=422,
                    detail=f"{resulting_category} is not valid for {resulting_mode.upper()}",
                )
            resulting_status = fields.get("status", existing["status"])
            if resulting_mode == "awdp" and resulting_status == "published":
                script_kinds = {
                    row["kind"]
                    for row in connection.execute(
                        "SELECT DISTINCT kind FROM assets WHERE challenge_id = ? "
                        "AND kind IN ('check_script', 'fix_script') AND validation_status = 'valid'",
                        (challenge_id,),
                    ).fetchall()
                }
                missing = {"check_script", "fix_script"} - script_kinds
                if missing:
                    names = ", ".join(sorted(kind.replace("_script", "").title() for kind in missing))
                    raise HTTPException(
                        status_code=422,
                        detail=f"Upload valid {names} scripts before publishing this AWDP challenge",
                    )
            fields["updated_at"] = datetime.now(UTC).isoformat()
            assignments = ", ".join(f"{key} = ?" for key in fields)
            cursor = connection.execute(
                f"UPDATE challenges SET {assignments} WHERE id = ?", (*fields.values(), challenge_id)
            )
            row = connection.execute(
                """
                SELECT c.*,
                       EXISTS(SELECT 1 FROM assets a WHERE a.challenge_id = c.id
                              AND a.kind = 'check_script' AND a.validation_status = 'valid')
                           AS check_script_configured,
                       EXISTS(SELECT 1 FROM assets a WHERE a.challenge_id = c.id
                              AND a.kind = 'fix_script' AND a.validation_status = 'valid')
                           AS fix_script_configured
                FROM challenges c WHERE c.id = ?
                """,
                (challenge_id,),
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Challenge update conflicts with existing data") from exc
    return _challenge(row)


@router.delete("/{challenge_id}", response_model=Message)
async def delete_challenge(
    challenge_id: str,
    _: AdminUser,
    database: DatabaseDep,
    settings: SettingsDep,
    docker: DockerDep,
) -> Message:
    with database.connect() as connection:
        challenge = connection.execute(
            "SELECT id FROM challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
        instances = connection.execute(
            "SELECT container_id FROM instances WHERE challenge_id = ? AND status IN ('starting', 'running')",
            (challenge_id,),
        ).fetchall()
        assets = connection.execute(
            "SELECT stored_name FROM assets WHERE challenge_id = ?", (challenge_id,)
        ).fetchall()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    for instance in instances:
        if instance["container_id"]:
            try:
                docker.stop(instance["container_id"])
            except ContainerError:
                pass
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM deployment_events WHERE instance_id IN "
            "(SELECT id FROM instances WHERE challenge_id = ?) OR asset_id IN "
            "(SELECT id FROM assets WHERE challenge_id = ?)",
            (challenge_id, challenge_id),
        )
        connection.execute("DELETE FROM submissions WHERE challenge_id = ?", (challenge_id,))
        connection.execute("DELETE FROM instances WHERE challenge_id = ?", (challenge_id,))
        connection.execute("DELETE FROM assets WHERE challenge_id = ?", (challenge_id,))
        connection.execute("DELETE FROM challenges WHERE id = ?", (challenge_id,))
    for asset in assets:
        (settings.storage_path / asset["stored_name"]).unlink(missing_ok=True)
    return Message(message="Challenge deleted")


@router.post("/{challenge_id}/attachments", response_model=AssetPublic, status_code=201)
async def upload_attachment(
    challenge_id: str,
    request: Request,
    _: AdminUser,
    database: DatabaseDep,
    settings: SettingsDep,
    filename: str = Query(min_length=1, max_length=120),
) -> AssetPublic:
    content = await request.body()
    if not content or len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Attachment is empty or exceeds the upload limit")
    with database.connect() as connection:
        if not connection.execute("SELECT 1 FROM challenges WHERE id = ?", (challenge_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Challenge not found")
    stored_name, _ = store_bytes(settings.storage_path, challenge_id, filename, content)
    asset_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO assets (
                id, challenge_id, user_id, kind, original_name, stored_name, size_bytes,
                validation_status, validation_output, created_at
            ) VALUES (?, ?, NULL, 'attachment', ?, ?, ?, 'valid', 'Attachment is available.', ?)
            """,
            (asset_id, challenge_id, safe_filename(filename), stored_name, len(content), now),
        )
        row = connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return _asset(row)


@router.get("/assets/{asset_id}/download")
async def download_asset(asset_id: str, user: CurrentUser, database: DatabaseDep, settings: SettingsDep):
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")
    if row["kind"] != "attachment" and row["user_id"] != user["id"] and user["role"] != "admin":
        raise HTTPException(status_code=404, detail="Asset not found")
    path = settings.storage_path / row["stored_name"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Asset file is missing")
    return Response(
        content=path.read_bytes(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{row["original_name"]}"'},
    )
