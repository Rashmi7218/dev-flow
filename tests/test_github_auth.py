import time

import jwt
import respx
from httpx import Response

from app.config import settings
from app.integrations import github_auth


def _mock_installation_and_token(installation_id: int = 999, token: str = "test-token", ttl: int = 3600):
    installation_route = respx.get(
        "https://api.github.com/repos/acme/widgets/installation"
    ).mock(return_value=Response(200, json={"id": installation_id}))
    expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + ttl))
    token_route = respx.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    ).mock(return_value=Response(201, json={"token": token, "expires_at": expires_at}))
    return installation_route, token_route


def test_app_jwt_has_expected_claims():
    token = github_auth._app_jwt()
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["iss"] == settings.github_app_id
    assert claims["exp"] - claims["iat"] <= 10 * 60


@respx.mock
async def test_get_installation_token_hits_both_endpoints():
    installation_route, token_route = _mock_installation_and_token()

    token = await github_auth.get_installation_token("acme/widgets")

    assert token == "test-token"
    assert installation_route.called
    assert token_route.called


@respx.mock
async def test_get_installation_token_is_cached():
    installation_route, token_route = _mock_installation_and_token()

    first = await github_auth.get_installation_token("acme/widgets")
    second = await github_auth.get_installation_token("acme/widgets")

    assert first == second == "test-token"
    assert installation_route.call_count == 1
    assert token_route.call_count == 1


@respx.mock
async def test_get_installation_token_refetches_after_expiry():
    _installation_route, token_route = _mock_installation_and_token(ttl=3600)

    await github_auth.get_installation_token("acme/widgets")
    # Force the cached entry to look expired without sleeping in the test.
    github_auth._token_cache["acme/widgets"]["expires_at"] = time.time() - 1
    await github_auth.get_installation_token("acme/widgets")

    assert token_route.call_count == 2
