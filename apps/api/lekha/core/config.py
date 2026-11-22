from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "Lekha"  # product name lives here (and in apps/web/lib/brand.ts)
API_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=API_ROOT / ".env", env_prefix="", extra="ignore")

    env: str = Field("development", alias="LEKHA_ENV")
    database_url: str = Field("postgresql+asyncpg://lekha:lekha@localhost:5433/lekha", alias="DATABASE_URL")
    # Role the app switches to for every connection so row-level security applies (superusers bypass RLS).
    db_app_role: str = Field("lekha_app", alias="LEKHA_DB_APP_ROLE")
    secret_key: str = Field("dev-secret-change-me-dev-secret-change-me", alias="LEKHA_SECRET_KEY")
    web_origin: str = Field("http://localhost:3000", alias="LEKHA_WEB_ORIGIN")
    public_api_url: str = Field("http://localhost:8000", alias="LEKHA_PUBLIC_API_URL")

    storage_backend: str = Field("local", alias="LEKHA_STORAGE")  # local | s3
    storage_dir: Path = Field(API_ROOT / ".storage", alias="LEKHA_STORAGE_DIR")
    s3_bucket: str = Field("lekha", alias="S3_BUCKET")
    s3_endpoint: str | None = Field(None, alias="S3_ENDPOINT")
    s3_region: str = Field("ap-south-1", alias="S3_REGION")
    s3_access_key: str | None = Field(None, alias="S3_ACCESS_KEY")
    s3_secret_key: str | None = Field(None, alias="S3_SECRET_KEY")

    ai_provider: str = Field("auto", alias="LEKHA_AI_PROVIDER")  # auto | gemini | claude
    gemini_api_key: str | None = Field(None, alias="GEMINI_API_KEY")
    # Tried in order; on quota/rate errors the next model is used, then offline extraction.
    gemini_models: str = Field("gemini-flash-latest,gemini-3.5-flash,gemini-flash-lite-latest", alias="LEKHA_GEMINI_MODELS")
    anthropic_api_key: str | None = Field(None, alias="ANTHROPIC_API_KEY")
    ai_model: str = Field("claude-opus-5-5", alias="LEKHA_AI_MODEL")
    ai_effort: str = Field("medium", alias="LEKHA_AI_EFFORT")
    ai_fallbacks: bool = Field(True, alias="LEKHA_AI_FALLBACKS")
    # Offline mode makes extraction use OCR + heuristics only (tests, demos without a key).
    ai_offline: bool = Field(False, alias="LEKHA_AI_OFFLINE")

    run_worker_in_api: bool = Field(True, alias="LEKHA_WORKER_IN_API")
    worker_concurrency: int = Field(3, alias="LEKHA_WORKER_CONCURRENCY")
    # Separate processes for OCR so it never blocks web requests (0 = inline thread, used in tests).
    ocr_processes: int = Field(2, alias="LEKHA_OCR_PROCESSES")
    # Demo workspace size (1.0 = ~20 invoices per client per month). Smaller on tiny hosts.
    demo_scale: float = Field(1.0, alias="LEKHA_DEMO_SCALE")
    max_upload_mb: int = Field(20, alias="LEKHA_MAX_UPLOAD_MB")
    session_days: int = Field(14, alias="LEKHA_SESSION_DAYS")
    auto_approve_confidence: float = Field(0.97, alias="LEKHA_AUTO_APPROVE")

    email_from: str = Field("Lekha <noreply@lekha.app>", alias="LEKHA_EMAIL_FROM")
    resend_api_key: str | None = Field(None, alias="RESEND_API_KEY")

    @property
    def is_prod(self) -> bool:
        return self.env == "production"

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
