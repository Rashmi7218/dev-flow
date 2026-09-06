import httpx

from app.config import settings

BASE_URL = "https://api.github.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
    }


async def get_pull_request(repo: str, number: int) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/repos/{repo}/pulls/{number}", headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def get_changed_files(repo: str, number: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/repos/{repo}/pulls/{number}/files", headers=_headers()
        )
        resp.raise_for_status()
        return resp.json()
