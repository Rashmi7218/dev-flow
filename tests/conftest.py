import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_test_app_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_test_app_key_pem = _test_app_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()

os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-github-secret")
os.environ.setdefault("GITHUB_APP_ID", "123456")
os.environ.setdefault("GITHUB_APP_PRIVATE_KEY", _test_app_key_pem)
os.environ.setdefault("GITHUB_APP_SLUG", "devflow-test-app")
os.environ.setdefault("JIRA_BASE_URL", "https://test.atlassian.net")
os.environ.setdefault("JIRA_EMAIL", "test@example.com")
os.environ.setdefault("JIRA_API_TOKEN", "test-jira-token")
os.environ.setdefault("JIRA_PROJECT_KEY", "TEST")
os.environ.setdefault("JIRA_WEBHOOK_TOKEN", "test-jira-webhook-token")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_SIGNING_SECRET", "test-slack-signing-secret")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db import Base, engine
from app.integrations import github_auth
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def _reset_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(autouse=True)
async def _reset_github_token_cache():
    github_auth._token_cache.clear()
    yield


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_client():
    transport = ASGITransport(app=app)
    auth = (os.environ["ADMIN_USERNAME"], os.environ["ADMIN_PASSWORD"])
    async with AsyncClient(transport=transport, base_url="http://test", auth=auth) as ac:
        yield ac
