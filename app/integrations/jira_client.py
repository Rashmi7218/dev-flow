import httpx

from app.config import settings


def _auth() -> tuple[str, str]:
    return (settings.jira_email, settings.jira_api_token)


def _doc(text: str) -> dict:
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


async def get_issue(key: str) -> dict:
    async with httpx.AsyncClient(auth=_auth()) as client:
        resp = await client.get(f"{settings.jira_base_url}/rest/api/3/issue/{key}")
        resp.raise_for_status()
        return resp.json()


async def create_issue(summary: str, description: str, issue_type: str = "Task") -> dict:
    payload = {
        "fields": {
            "project": {"key": settings.jira_project_key},
            "summary": summary,
            "description": _doc(description),
            "issuetype": {"name": issue_type},
        }
    }
    async with httpx.AsyncClient(auth=_auth()) as client:
        resp = await client.post(f"{settings.jira_base_url}/rest/api/3/issue", json=payload)
        resp.raise_for_status()
        return resp.json()


async def add_comment(key: str, body: str) -> dict:
    async with httpx.AsyncClient(auth=_auth()) as client:
        resp = await client.post(
            f"{settings.jira_base_url}/rest/api/3/issue/{key}/comment",
            json={"body": _doc(body)},
        )
        resp.raise_for_status()
        return resp.json()
