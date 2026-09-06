import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.correlation import extract_ticket_key
from app.db import get_db
from app.integrations import github_client, groq_client, slack_client
from app.models import Event, PullRequest, WorkflowRun
from app.security import verify_github_signature

router = APIRouter(prefix="/webhooks/github", tags=["github"])


@router.post("")
async def handle_github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    body: bytes = Depends(verify_github_signature),
):
    event_type = request.headers.get("X-GitHub-Event", "")
    payload = json.loads(body)

    if event_type == "pull_request":
        await _handle_pull_request(db, payload)
    elif event_type == "workflow_run":
        await _handle_workflow_run(db, payload)

    return {"status": "accepted"}


async def _handle_pull_request(db: AsyncSession, payload: dict) -> None:
    action = payload.get("action")
    pr = payload["pull_request"]
    repo = payload["repository"]["full_name"]

    ticket_key = extract_ticket_key(
        pr.get("head", {}).get("ref"), pr.get("title"), pr.get("body")
    )
    status = "merged" if pr.get("merged") else action

    db.add(
        Event(
            source="github",
            event_type=f"pull_request.{action}",
            payload=payload,
            ticket_key=ticket_key,
        )
    )
    db.add(
        PullRequest(
            repo=repo,
            number=pr["number"],
            title=pr["title"],
            url=pr["html_url"],
            author=pr["user"]["login"],
            status=status,
            ticket_key=ticket_key,
        )
    )
    await db.commit()

    if action == "opened":
        text = (
            f"{ticket_key or repo} — PR Opened\n\n"
            f"#{pr['number']} {pr['title']}\nAuthor: {pr['user']['login']}"
        )
        try:
            files = await github_client.get_changed_files(repo, pr["number"])
            summary = await groq_client.summarize_pr(pr["title"], pr.get("body"), files)
            text += f"\n\n*Summary:* {summary}"
        except Exception:
            pass
        await slack_client.post_message(text)
    elif pr.get("merged"):
        await slack_client.post_message(
            f"{ticket_key or repo} — PR Merged ✅\n\n"
            f"#{pr['number']} {pr['title']}\nAuthor: {pr['user']['login']}"
        )


async def _handle_workflow_run(db: AsyncSession, payload: dict) -> None:
    if payload.get("action") != "completed":
        return

    run = payload["workflow_run"]
    repo = payload["repository"]["full_name"]
    ticket_key = extract_ticket_key(run.get("head_branch"), run.get("display_title"))
    conclusion = run.get("conclusion")

    db.add(
        Event(
            source="github",
            event_type="workflow_run.completed",
            payload=payload,
            ticket_key=ticket_key,
        )
    )
    db.add(
        WorkflowRun(
            repo=repo,
            run_id=run["id"],
            name=run["name"],
            status=run["status"],
            conclusion=conclusion,
            ticket_key=ticket_key,
        )
    )
    await db.commit()

    label = "✅ Succeeded" if conclusion == "success" else f"\U0001f6a8 Failed ({conclusion})"
    text = f"{ticket_key or repo} — Workflow {run['name']} {label}\n\n{run['html_url']}"

    if conclusion not in ("success", None):
        try:
            jobs = await github_client.get_workflow_run_jobs(repo, run["id"])
            explanation = await groq_client.explain_failure(run["name"], jobs)
            text += (
                f"\n\n*Likely cause:* {explanation['likely_cause']}"
                f"\n*Failed stage:* {explanation['failed_stage']}"
                f"\n*Suggested action:* {explanation['suggested_action']}"
            )
        except Exception:
            pass

    await slack_client.post_message(text)
