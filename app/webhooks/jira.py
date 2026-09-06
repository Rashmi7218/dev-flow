from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.integrations import slack_client
from app.models import Event, Issue

router = APIRouter(prefix="/webhooks/jira", tags=["jira"])


@router.post("")
async def handle_jira_webhook(
    request: Request, token: str, db: AsyncSession = Depends(get_db)
):
    if token != settings.jira_webhook_token:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    payload = await request.json()
    webhook_event = payload.get("webhookEvent", "")
    issue = payload.get("issue")
    if not issue:
        return {"status": "ignored"}

    key = issue["key"]
    fields = issue["fields"]
    status = fields["status"]["name"]
    summary = fields["summary"]
    project = fields["project"]["key"]

    db.add(Event(source="jira", event_type=webhook_event, payload=payload, ticket_key=key))

    result = await db.execute(select(Issue).where(Issue.key == key))
    existing = result.scalar_one_or_none()
    if existing:
        existing.status = status
        existing.summary = summary
        existing.project = project
    else:
        db.add(Issue(key=key, project=project, summary=summary, status=status))

    await db.commit()

    changelog = payload.get("changelog", {})
    status_changed = any(item.get("field") == "status" for item in changelog.get("items", []))
    if status_changed:
        await slack_client.post_message(f"{key} — Status Updated\n\nNew Status: {status}")

    return {"status": "accepted"}
