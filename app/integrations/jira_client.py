import httpx

from app.config import settings


def _auth() -> tuple[str, str]:
    return (settings.jira_email, settings.jira_api_token)


def _doc(text: str) -> dict:
    paragraphs = []
    for para in text.split("\n\n"):
        content = []
        for i, line in enumerate(para.split("\n")):
            if i > 0:
                content.append({"type": "hardBreak"})
            content.append({"type": "text", "text": line or " "})
        paragraphs.append({"type": "paragraph", "content": content})
    return {"type": "doc", "version": 1, "content": paragraphs}


async def get_issue(key: str) -> dict:
    async with httpx.AsyncClient(auth=_auth()) as client:
        resp = await client.get(f"{settings.jira_base_url}/rest/api/3/issue/{key}")
        resp.raise_for_status()
        return resp.json()


async def create_issue(
    summary: str,
    description: str,
    issue_type: str = "Task",
    project_key: str | None = None,
) -> dict:
    payload = {
        "fields": {
            "project": {"key": project_key or settings.jira_project_key},
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
