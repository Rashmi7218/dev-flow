from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://devflow:devflow@localhost:5432/devflow"

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg_driver(cls, v: str) -> str:
        # Managed Postgres providers (e.g. Render) hand back a bare postgres:// /
        # postgresql:// URL; SQLAlchemy's async engine needs the asyncpg dialect prefix.
        if v.startswith("postgres://"):
            return "postgresql+asyncpg://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            return "postgresql+asyncpg://" + v[len("postgresql://") :]
        return v

    github_webhook_secret: str
    github_token: str

    jira_base_url: str
    jira_email: str
    jira_api_token: str
    jira_project_key: str
    jira_webhook_token: str

    slack_bot_token: str
    slack_signing_secret: str
    slack_default_channel: str = "#engineering"

    groq_api_key: str
    groq_model: str = "openai/gpt-oss-120b"


settings = Settings()
