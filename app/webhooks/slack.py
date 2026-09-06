import json
from urllib.parse import parse_qsl

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends

from app.config import settings
from app.integrations import jira_client
from app.security import verify_slack_signature

router = APIRouter(prefix="/webhooks/slack", tags=["slack"])


@router.post("/events")
async def handle_slack_events(body: bytes = Depends(verify_slack_signature)):
    payload = json.loads(body)
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}
    return {"status": "accepted"}


async def _create_issue_and_notify(summary: str, requester: str, response_url: str) -> None:
    try:
        issue = await jira_client.create_issue(summary=summary, description=summary)
        url = f"{settings.jira_base_url}/browse/{issue['key']}"
        payload = {
            "response_type": "in_channel",
            "text": f"✅ Jira issue {issue['key']} created",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"✅ *Jira issue <{url}|{issue['key']}> created*\n{summary}",
                    },
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": f"Requested by *{requester}*"}],
                },
            ],
        }
    except Exception:
        payload = {
            "response_type": "ephemeral",
            "text": f"❌ Failed to create Jira issue for: {summary}",
        }

    async with httpx.AsyncClient() as client:
        await client.post(response_url, json=payload)


@router.post("/commands")
async def handle_slack_command(
    background_tasks: BackgroundTasks,
    body: bytes = Depends(verify_slack_signature),
):
    form = dict(parse_qsl(body.decode()))
    text = form.get("text", "").strip()
    response_url = form.get("response_url", "")
    requester = form.get("user_name", "someone")

    parts = text.split(maxsplit=1)
    if len(parts) < 2 or parts[0] != "create":
        return {"response_type": "ephemeral", "text": "Usage: /devflow create <summary>"}

    summary = parts[1].strip()
    background_tasks.add_task(_create_issue_and_notify, summary, requester, response_url)
    return {"response_type": "ephemeral", "text": f"Creating Jira issue for: {summary}..."}
