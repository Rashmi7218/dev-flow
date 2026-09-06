from app.config import Settings

_REQUIRED = {
    "github_webhook_secret": "s",
    "github_token": "t",
    "jira_base_url": "https://x.atlassian.net",
    "jira_email": "a@b.com",
    "jira_api_token": "t",
    "jira_project_key": "KAN",
    "jira_webhook_token": "t",
    "slack_bot_token": "t",
    "slack_signing_secret": "t",
    "groq_api_key": "t",
}


def test_bare_postgres_scheme_gets_asyncpg_driver():
    s = Settings(database_url="postgres://user:pass@host/db", **_REQUIRED)
    assert s.database_url == "postgresql+asyncpg://user:pass@host/db"


def test_bare_postgresql_scheme_gets_asyncpg_driver():
    s = Settings(database_url="postgresql://user:pass@host/db", **_REQUIRED)
    assert s.database_url == "postgresql+asyncpg://user:pass@host/db"


def test_already_asyncpg_scheme_is_untouched():
    s = Settings(database_url="postgresql+asyncpg://user:pass@host/db", **_REQUIRED)
    assert s.database_url == "postgresql+asyncpg://user:pass@host/db"


def test_sqlite_scheme_is_untouched():
    s = Settings(database_url="sqlite+aiosqlite:///:memory:", **_REQUIRED)
    assert s.database_url == "sqlite+aiosqlite:///:memory:"
