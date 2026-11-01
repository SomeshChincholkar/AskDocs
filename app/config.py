"""Application configuration loaded from environment variables and .env."""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for AskDocs."""

    database_url: str = ""
    gemini_api_key: str = ""
    embedding_model: str = "gemini-embedding-001"
    llm_model: str = "gemini-2.5-flash"
    max_upload_mb: int = Field(default=10, gt=0)
    max_files_per_session: int = Field(default=5, gt=0)
    rate_limit_questions_per_hour: int = Field(default=30, gt=0)
    rate_limit_uploads_per_hour: int = Field(default=10, gt=0)
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=120, ge=0)
    top_k: int = Field(default=6, gt=0)
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = Field(default=30, gt=0)
    anon_jwt_expire_days: int = Field(default=365, gt=0)
    login_attempts_per_15_min: int = Field(default=10, gt=0)
    anon_create_per_hour: int = Field(default=20, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_chunk_overlap(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE.")
        return self

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters long.")
        return value

    @field_validator("jwt_algorithm")
    @classmethod
    def validate_jwt_algorithm(cls, value: str) -> str:
        if value not in {"HS256", "HS384", "HS512"}:
            raise ValueError("JWT_ALGORITHM must be HS256, HS384, or HS512.")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
