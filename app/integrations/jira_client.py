import re

import httpx

from app.config import settings

_BOLD_RE = re.compile(r"\*([^*\n]+)\*")
_BULLET_RE = re.compile(r"^[-•]\s+(.*)")


def _auth() -> tuple[str, str]:
    return (settings.jira_email, settings.jira_api_token)


def _text_runs(line: str) -> list[dict]:
    """Split a line into ADF text nodes, turning *bold* spans (Slack's own bold syntax,
    which is what shows up in a slash command's plain-text payload) into a strong mark."""
    runs = []
    pos = 0
    for match in _BOLD_RE.finditer(line):
        if match.start() > pos:
            runs.append({"type": "text", "text": line[pos : match.start()]})
        runs.append({"type": "text", "text": match.group(1), "marks": [{"type": "strong"}]})
        pos = match.end()
    if pos < len(line):
        runs.append({"type": "text", "text": line[pos:]})
    return runs or [{"type": "text", "text": line or " "}]


def _render_block(block: str) -> list[dict]:
    nodes: list[dict] = []
    paragraph_lines: list[str] = []
    bullet_items: list[dict] = []

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        content = []
        for i, line in enumerate(paragraph_lines):
            if i > 0:
                content.append({"type": "hardBreak"})
            content.extend(_text_runs(line))
        nodes.append({"type": "paragraph", "content": content})
        paragraph_lines.clear()

    def flush_bullets() -> None:
        if bullet_items:
            nodes.append({"type": "bulletList", "content": list(bullet_items)})
            bullet_items.clear()

    for line in block.split("\n"):
        bullet_match = _BULLET_RE.match(line.strip())
        if bullet_match:
            flush_paragraph()
            bullet_items.append(
                {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": _text_runs(bullet_match.group(1))}],
                }
            )
        else:
            flush_bullets()
            paragraph_lines.append(line)

    flush_paragraph()
    flush_bullets()
    return nodes or [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}]


def _doc(text: str) -> dict:
    content = []
    for block in text.split("\n\n"):
        content.extend(_render_block(block))
    return {"type": "doc", "version": 1, "content": content}


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
            value = node.get("text", "")
            if any(mark.get("type") == "strong" for mark in node.get("marks", [])):
                return f"*{value}*"
            return value
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
