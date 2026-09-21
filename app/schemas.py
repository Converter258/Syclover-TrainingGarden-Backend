from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

CTF_CATEGORIES = ("Web", "Pwn", "Reverse", "Misc", "Crypto")
AWDP_CATEGORIES = ("Web", "Pwn")


def category_is_valid(mode: str, category: str) -> bool:
    allowed = AWDP_CATEGORIES if mode == "awdp" else CTF_CATEGORIES
    return category in allowed


class Message(BaseModel):
    message: str


class UserPublic(BaseModel):
    id: str
    username: str
    role: Literal["player", "admin"]
    is_active: bool
    created_at: datetime


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class UserUpdate(BaseModel):
    role: Literal["player", "admin"] | None = None
    is_active: bool | None = None


class ChallengeCreate(BaseModel):
    title: str = Field(min_length=2, max_length=100)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    description: str = Field(min_length=10, max_length=10_000)
    category: Literal["Web", "Pwn", "Reverse", "Misc", "Crypto"]
    mode: Literal["ctf", "awdp"]
    difficulty: Literal["noob", "easy", "normal", "hard", "insane"]
    points: int = Field(ge=1, le=10_000)
    docker_image: str | None = Field(default=None, max_length=255)
    internal_port: int | None = Field(default=None, ge=1, le=65535)
    flag: str = Field(min_length=3, max_length=512)
    status: Literal["draft", "published", "archived"] = "draft"

    @field_validator("docker_image")
    @classmethod
    def normalize_image(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @model_validator(mode="after")
    def validate_category_for_mode(self):
        if not category_is_valid(self.mode, self.category):
            raise ValueError(f"{self.category} is not a valid category for {self.mode.upper()}")
        return self


class ChallengeUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, min_length=10, max_length=10_000)
    category: Literal["Web", "Pwn", "Reverse", "Misc", "Crypto"] | None = None
    mode: Literal["ctf", "awdp"] | None = None
    difficulty: Literal["noob", "easy", "normal", "hard", "insane"] | None = None
    points: int | None = Field(default=None, ge=1, le=10_000)
    docker_image: str | None = Field(default=None, max_length=255)
    internal_port: int | None = Field(default=None, ge=1, le=65535)
    flag: str | None = Field(default=None, min_length=3, max_length=512)
    status: Literal["draft", "published", "archived"] | None = None


class AssetPublic(BaseModel):
    id: str
    challenge_id: str
    user_id: str | None
    kind: Literal["attachment", "patch", "check_script", "fix_script"]
    original_name: str
    size_bytes: int
    validation_status: Literal["pending", "valid", "invalid"]
    validation_output: str | None
    created_at: datetime
    download_url: str


class ChallengePublic(BaseModel):
    id: str
    title: str
    slug: str
    description: str
    category: str
    mode: Literal["ctf", "awdp"]
    difficulty: Literal["noob", "easy", "normal", "hard", "insane"]
    points: int
    docker_image: str | None
    internal_port: int | None
    status: Literal["draft", "published", "archived"]
    solved: bool = False
    attachments: list[AssetPublic] = Field(default_factory=list)
    check_script_configured: bool = False
    fix_script_configured: bool = False


class InstancePublic(BaseModel):
    id: str
    challenge_id: str
    challenge_title: str | None = None
    public_host: str | None
    public_port: int | None
    status: Literal["starting", "running", "stopped", "failed"]
    error_message: str | None
    expires_at: datetime
    created_at: datetime


class SubmissionRequest(BaseModel):
    flag: str = Field(min_length=1, max_length=512)


class SubmissionResult(BaseModel):
    correct: bool
    awarded_points: int
    message: str


class ScoreEntry(BaseModel):
    rank: int
    user_id: str
    username: str
    score: int
    solves: int
    last_solve_at: datetime | None


class ScoreboardResponse(BaseModel):
    participants: int
    published_challenges: int
    total_solves: int
    rankings: list[ScoreEntry]


class DeploymentEventPublic(BaseModel):
    id: str
    instance_id: str
    asset_id: str
    success: bool
    output: str
    created_at: datetime
