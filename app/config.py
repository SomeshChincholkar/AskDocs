"""Application configuration loaded from environment variables and .env."""

from functools import lru_cache

from pydantic import Field, model_validator
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
    cookie_name: str = "anon_user_id"
    secure_cookies: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @model_validator(mode="after")
    def validate_chunk_overlap(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE.")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
