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


def _flatten_adf(doc: dict | None) -> str:
    """Best-effort plain-text rendering of a Jira ADF doc, for re-feeding into _doc().

    Handles the node types _doc() itself produces (paragraph, text, hardBreak) plus the
    common ones Jira's own rich-text editor adds (heading, bulletList/orderedList,
    listItem). Anything else is walked generically via its "content" children, so
    unrecognized node types degrade to "lose formatting" rather than crashing.
    """
    if not doc:
        return ""

    def walk(node: dict) -> str:
        node_type = node.get("type")
        if node_type == "text":
            return node.get("text", "")
        if node_type == "hardBreak":
            return "\n"
        children = "".join(walk(c) for c in node.get("content", []))
        if node_type in ("paragraph", "heading"):
            return children + "\n\n"
        if node_type == "listItem":
            return f"- {children.strip()}\n"
        if node_type in ("bulletList", "orderedList"):
            return children + "\n"
        return children

    return walk(doc).strip()


async def append_description(key: str, new_text: str) -> None:
    issue = await get_issue(key)
    existing_text = _flatten_adf(issue["fields"].get("description"))
    combined = f"{existing_text}\n\n{new_text}" if existing_text else new_text

    async with httpx.AsyncClient(auth=_auth()) as client:
        resp = await client.put(
            f"{settings.jira_base_url}/rest/api/3/issue/{key}",
            json={"fields": {"description": _doc(combined)}},
        )
        resp.raise_for_status()


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


async def get_transitions(key: str) -> list[dict]:
    async with httpx.AsyncClient(auth=_auth()) as client:
        resp = await client.get(f"{settings.jira_base_url}/rest/api/3/issue/{key}/transitions")
        resp.raise_for_status()
        return resp.json()["transitions"]


async def transition_issue(key: str, transition_id: str) -> None:
    async with httpx.AsyncClient(auth=_auth()) as client:
        resp = await client.post(
            f"{settings.jira_base_url}/rest/api/3/issue/{key}/transitions",
            json={"transition": {"id": transition_id}},
        )
        resp.raise_for_status()
