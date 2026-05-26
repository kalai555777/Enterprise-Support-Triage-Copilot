from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    HF_TOKEN: str | None = None
    GITHUB_PAT: str | None = None
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str = "estc-dev"
    POSTGRES_USER: str = "estc"
    POSTGRES_PASSWORD: str = "estc_dev_pw"
    POSTGRES_DB: str = "estc"
    POSTGRES_HOST: str = "mcp-postgres"
    POSTGRES_PORT: int = 5432
    CLASSIFIER_API_URL: str = "http://classifier-api:8001"

    # Tell Pydantic to read from the .env file in the root directory
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")