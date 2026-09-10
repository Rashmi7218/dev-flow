import calendar
import time

import httpx
import jwt

from app.config import settings

BASE_URL = "https://api.github.com"

_JWT_TTL_SECONDS = 9 * 60  # GitHub caps this at 10 minutes; leave a safety margin
_TOKEN_REFRESH_MARGIN_SECONDS = 60

_token_cache: dict[str, dict] = {}


def _app_jwt() -> str:
    now = int(time.time())
    payload = {"iat": now - 30, "exp": now + _JWT_TTL_SECONDS, "iss": settings.github_app_id}
    return jwt.encode(payload, settings.github_app_private_key, algorithm="RS256")


async def _installation_id(repo: str) -> int:
    headers = {
        "Authorization": f"Bearer {_app_jwt()}",
        "Accept": "application/vnd.github+json",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/repos/{repo}/installation", headers=headers)
        resp.raise_for_status()
        return resp.json()["id"]


async def get_installation_token(repo: str) -> str:
    cached = _token_cache.get(repo)
    if cached and cached["expires_at"] > time.time() + _TOKEN_REFRESH_MARGIN_SECONDS:
        return cached["token"]

    installation_id = await _installation_id(repo)
    headers = {
        "Authorization": f"Bearer {_app_jwt()}",
        "Accept": "application/vnd.github+json",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/app/installations/{installation_id}/access_tokens", headers=headers
        )
        resp.raise_for_status()
        data = resp.json()

    token = data["token"]
    expires_at = calendar.timegm(time.strptime(data["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
    _token_cache[repo] = {"token": token, "expires_at": expires_at}
    return token
