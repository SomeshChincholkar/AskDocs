"""Typed request and response models."""

from datetime import datetime
from uuid import UUID

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserInfo(BaseModel):
    user_id: UUID
    is_anonymous: bool


class AuthCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=8, max_length=128)
    model_config = ConfigDict(str_strip_whitespace=False)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", value):
            raise ValueError(
                "Username must be 3-30 characters and contain only letters, digits, "
                "underscores, dots, or dashes."
            )
        return value


class AuthUserInfo(BaseModel):
    id: UUID
    username: str | None
    is_anonymous: bool


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: AuthUserInfo
    merged: dict[str, int] | None = None


class AuthMe(BaseModel):
    user_id: UUID
    username: str | None
    is_anonymous: bool


class SessionCreate(BaseModel):
    title: str = Field(default="New chat", max_length=120)
    model_config = ConfigDict(str_strip_whitespace=True)


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    model_config = ConfigDict(str_strip_whitespace=True)


class SessionInfo(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class DocumentInfo(BaseModel):
    id: UUID
    filename: str
    mime_type: str
    size_bytes: int
    page_count: int | None
    chunk_count: int
    status: str
    error: str | None
    created_at: datetime


class DocumentStatus(BaseModel):
    status: str
    chunk_count: int
    error: str | None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)


class MessageInfo(BaseModel):
    id: UUID
    role: str
    content: str
    sources: list[dict[str, object]]
    created_at: datetime
