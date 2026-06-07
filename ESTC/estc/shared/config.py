import os

from pydantic_settings import BaseSettings, SettingsConfigDict

# Production secret source: when secrets are mounted as files (Docker/K8s secrets,
# one file per setting, e.g. /run/secrets/POSTGRES_PASSWORD), pydantic-settings reads
# them automatically and they take precedence over .env. Falls back to None in dev so
# nothing breaks when the directory is absent.
_SECRETS_DIR = os.getenv("ESTC_SECRETS_DIR") or (
    "/run/secrets" if os.path.isdir("/run/secrets") else None
)


class Settings(BaseSettings):
    HF_TOKEN: str | None = None
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    GITHUB_PAT: str | None = None
    ESTC_GITHUB_REPO: str = "kalai555777/Enterprise-Support-Triage-Copilot"
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str = "estc-dev"
    LANGSMITH_TRACING: bool = False
    POSTGRES_USER: str = "estc"
    POSTGRES_PASSWORD: str = "estc_dev_pw"
    POSTGRES_DB: str = "estc"
    POSTGRES_HOST: str = "mcp-postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_READER_USER: str = "estc_reader"
    POSTGRES_READER_PASSWORD: str = "estc_reader_dev_pw"
    CLASSIFIER_API_URL: str = "http://classifier-api:8001"
    # Inter-service shared secret (empty = auth disabled, the offline/CI default).
    ESTC_API_KEY: str | None = None
    # Per-IP requests/minute on public endpoints (0 = disabled).
    ESTC_RATE_LIMIT_PER_MIN: int = 0
    # Durable LangGraph checkpointing via Postgres (falls back to in-memory if off/unavailable).
    ESTC_PERSIST_POSTGRES: bool = False

    # Read from .env (dev) and, when present, file-mounted secrets (prod).
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        secrets_dir=_SECRETS_DIR,
    )
