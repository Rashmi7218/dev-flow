import httpx

from app.integrations import github_auth

BASE_URL = "https://api.github.com"


async def _headers(repo: str) -> dict:
    token = await github_auth.get_installation_token(repo)
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


async def get_pull_request(repo: str, number: int) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/repos/{repo}/pulls/{number}", headers=await _headers(repo)
        )
        resp.raise_for_status()
        return resp.json()


async def get_changed_files(repo: str, number: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/repos/{repo}/pulls/{number}/files", headers=await _headers(repo)
        )
        resp.raise_for_status()
        return resp.json()


async def get_workflow_run_jobs(repo: str, run_id: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/repos/{repo}/actions/runs/{run_id}/jobs", headers=await _headers(repo)
        )
        resp.raise_for_status()
        return resp.json()["jobs"]
