import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.integrations import jira_client
from app.models import Event, Issue, PullRequest, WorkflowRun

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

_DASHBOARD_HTML = (Path(__file__).parent / "static" / "dashboard.html").read_text()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page() -> str:
    return _DASHBOARD_HTML


@router.get("/api/events")
async def list_events(
    limit: int = 50,
    source: str | None = None,
    ticket_key: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    query = select(Event)
    if source:
        query = query.where(Event.source == source)
    if ticket_key:
        query = query.where(Event.ticket_key == ticket_key.upper())
    query = query.order_by(Event.id.desc()).limit(limit)

    result = await db.execute(query)
    events = result.scalars().all()
    return [
        {
            "id": e.id,
            "source": e.source,
            "event_type": e.event_type,
            "ticket_key": e.ticket_key,
            "received_at": e.received_at.isoformat(),
        }
        for e in events
    ]


@router.get("/api/tickets/recent")
async def recent_tickets(limit: int = 8, db: AsyncSession = Depends(get_db)):
    limit = max(1, min(limit, 50))
    result = await db.execute(
        select(Event.ticket_key)
        .where(Event.ticket_key.is_not(None))
        .order_by(Event.id.desc())
        .limit(200)
    )
    seen: list[str] = []
    for (key,) in result.all():
        if key not in seen:
            seen.append(key)
        if len(seen) >= limit:
            break
    return seen


@router.get("/api/tickets/{key}/timeline")
async def ticket_timeline(key: str, db: AsyncSession = Depends(get_db)):
    key = key.upper()

    pr_result = await db.execute(
        select(PullRequest).where(PullRequest.ticket_key == key).order_by(PullRequest.updated_at)
    )
    prs = pr_result.scalars().all()

    run_result = await db.execute(
        select(WorkflowRun).where(WorkflowRun.ticket_key == key).order_by(WorkflowRun.updated_at)
    )
    runs = run_result.scalars().all()

    issue_result = await db.execute(select(Issue).where(Issue.key == key))
    local_issue = issue_result.scalar_one_or_none()

    jira_status = None
    jira_summary = None
    try:
        issue = await jira_client.get_issue(key)
        jira_status = issue["fields"]["status"]["name"]
        jira_summary = issue["fields"]["summary"]
    except Exception:
        logger.exception("Failed to fetch live Jira status for %s", key)
        if local_issue:
            jira_status = local_issue.status
            jira_summary = local_issue.summary

    if jira_status is None and not prs and not runs:
        raise HTTPException(status_code=404, detail=f"No data found for {key}")

    timeline = []
    for pr in prs:
        timeline.append(
            {
                "timestamp": pr.updated_at.isoformat(),
                "kind": "pull_request",
                "label": f"PR #{pr.number} {pr.status}",
                "detail": pr.title,
                "url": pr.url,
            }
        )
    for run in runs:
        timeline.append(
            {
                "timestamp": run.updated_at.isoformat(),
                "kind": "workflow_run",
                "label": f"Workflow {run.name}: {run.conclusion or run.status}",
                "detail": run.name,
                "url": None,
            }
        )
    timeline.sort(key=lambda e: e["timestamp"])

    return {
        "ticket_key": key,
        "jira_status": jira_status,
        "jira_summary": jira_summary,
        "timeline": timeline,
    }
