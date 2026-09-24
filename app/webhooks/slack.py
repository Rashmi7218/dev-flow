import json
import logging
import re
from urllib.parse import parse_qsl

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop import resume_after_approval, run_agent
from app.config import settings
from app.correlation import extract_ticket_key
from app.db import SessionLocal, get_db
from app.idempotency import is_duplicate_delivery
from app.integrations import groq_client, jira_client, slack_client
from app.models import AgentRun, PullRequest, WorkflowRun
from app.routing import jira_project_for_channel
from app.security import verify_slack_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/slack", tags=["slack"])

MENTION_RE = re.compile(r"<@\w+>")
TICKET_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b", re.IGNORECASE)


@router.post("/events")
async def handle_slack_events(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    body: bytes = Depends(verify_slack_signature),
):
    payload = json.loads(body)
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    if payload.get("type") == "event_callback":
        event_id = payload.get("event_id")
        if event_id and await is_duplicate_delivery(db, f"slack:{event_id}"):
            return {"status": "duplicate"}
        await db.commit()

        event = payload.get("event", {})
        if event.get("type") == "app_mention":
            raw_text = MENTION_RE.sub("", event.get("text", "")).strip()
            text = raw_text.lower()
            thread_ts = event.get("thread_ts")
            channel = event.get("channel")

            ticket_match = TICKET_KEY_RE.search(raw_text)
            if ticket_match:
                background_tasks.add_task(
                    _answer_status_query,
                    channel,
                    thread_ts or event.get("ts"),
                    ticket_match.group(1).upper(),
                )
            elif thread_ts and ("ticket" in text or "issue" in text):
                background_tasks.add_task(_propose_ticket_from_thread, channel, thread_ts)
            elif not thread_ts:
                await slack_client.post_message(
                    "Mention me with a ticket key (e.g. \"what's the status of KAN-4\") for a "
                    "status update, or inside a thread with \"create ticket from this thread\" "
                    "to turn that discussion into a Jira ticket.",
                    channel=channel,
                    thread_ts=event.get("ts"),
                )

    return {"status": "accepted"}


async def _answer_status_query(channel: str, thread_ts: str, ticket_key: str) -> None:
    async with SessionLocal() as db:
        pr_result = await db.execute(
            select(PullRequest)
            .where(PullRequest.ticket_key == ticket_key)
            .order_by(PullRequest.updated_at.desc())
            .limit(1)
        )
        latest_pr = pr_result.scalar_one_or_none()

        run_result = await db.execute(
            select(WorkflowRun)
            .where(WorkflowRun.ticket_key == ticket_key)
            .order_by(WorkflowRun.updated_at.desc())
            .limit(1)
        )
        latest_run = run_result.scalar_one_or_none()

    try:
        issue = await jira_client.get_issue(ticket_key)
        jira_status = issue["fields"]["status"]["name"]
        jira_summary = issue["fields"]["summary"]
    except Exception:
        logger.exception("Failed to fetch Jira issue %s", ticket_key)
        jira_status = None
        jira_summary = None

    if jira_status is None and latest_pr is None and latest_run is None:
        await slack_client.post_message(
            f"I couldn't find anything for {ticket_key}.", channel=channel, thread_ts=thread_ts
        )
        return

    facts = [f"Ticket: {ticket_key}"]
    lines = [f"*{ticket_key}*"]
    if jira_summary:
        lines.append(jira_summary)
    lines.append("")
    lines.append(f"Jira: {jira_status or 'Not found'}")
    facts.append(f"Jira status: {jira_status or 'unknown'}")

    if latest_pr:
        lines.append(f"PR #{latest_pr.number}: {latest_pr.status.capitalize()} — {latest_pr.url}")
        facts.append(f"Latest PR #{latest_pr.number} status: {latest_pr.status}")
    else:
        lines.append("PR: No linked pull request seen yet")

    if latest_run:
        conclusion = latest_run.conclusion or latest_run.status
        lines.append(f"Latest CI run ({latest_run.name}): {conclusion}")
        facts.append(f"Latest CI run \"{latest_run.name}\" conclusion: {conclusion}")
    else:
        lines.append("CI: No workflow runs seen yet")

    try:
        update = await groq_client.phrase_status_update("\n".join(facts))
        lines.append("")
        lines.append(f"_{update}_")
    except Exception:
        logger.exception("Failed to generate status update phrasing for %s", ticket_key)

    await slack_client.post_message("\n".join(lines), channel=channel, thread_ts=thread_ts)


async def _propose_ticket_from_thread(channel: str, thread_ts: str) -> None:
    try:
        messages = await slack_client.get_thread_replies(channel, thread_ts)
        ticket = await groq_client.extract_ticket_from_thread(messages)
        thread_url = await slack_client.get_permalink(channel, thread_ts)
    except Exception:
        logger.exception("Failed to build ticket proposal for thread %s", thread_ts)
        await slack_client.post_message(
            "Sorry, I couldn't generate a ticket from this thread.",
            channel=channel,
            thread_ts=thread_ts,
        )
        return

    ticket["thread_url"] = thread_url
    value = json.dumps(ticket)

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Create this Jira ticket from the thread?*\n\n"
                    f"*Title:* {ticket['title']}\n"
                    f"*Description:* {ticket['description']}\n"
                    f"*Steps to Reproduce:* {ticket['steps_to_reproduce']}\n"
                    f"*Expected Behavior:* {ticket['expected_behavior']}\n"
                    f"*Actual Behavior:* {ticket['actual_behavior']}\n"
                    f"*Severity:* {ticket['severity']}  *Priority:* {ticket['priority']}\n"
                    f"*Suggested Assignee/Team:* {ticket['suggested_assignee']}"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Create Ticket"},
                    "style": "primary",
                    "action_id": "approve_ticket",
                    "value": value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Cancel"},
                    "style": "danger",
                    "action_id": "cancel_ticket",
                },
            ],
        },
    ]
    await slack_client.post_message(
        "Create this Jira ticket from the thread?", channel=channel, thread_ts=thread_ts, blocks=blocks
    )


def _build_description(ticket: dict) -> str:
    return (
        f"{ticket['description']}\n\n"
        f"Steps to Reproduce:\n{ticket['steps_to_reproduce']}\n\n"
        f"Expected Behavior: {ticket['expected_behavior']}\n"
        f"Actual Behavior: {ticket['actual_behavior']}\n"
        f"Severity: {ticket['severity']}\n"
        f"Suggested Assignee/Team: {ticket['suggested_assignee']}\n\n"
        f"Source thread: {ticket['thread_url']}"
    )


async def _create_thread_ticket_and_notify(channel: str, ticket: dict, response_url: str) -> None:
    try:
        async with SessionLocal() as db:
            project_key = await jira_project_for_channel(db, channel)
        issue = await jira_client.create_issue(
            summary=ticket["title"],
            description=_build_description(ticket),
            project_key=project_key,
        )
        url = f"{settings.jira_base_url}/browse/{issue['key']}"
        text = f"✅ Jira issue <{url}|{issue['key']}> created from this thread."
    except Exception:
        logger.exception("Failed to create Jira ticket from thread: %s", ticket.get("title"))
        text = "❌ Failed to create the Jira ticket."

    async with httpx.AsyncClient() as client:
        await client.post(response_url, json={"replace_original": True, "text": text})


@router.post("/interactions")
async def handle_slack_interaction(
    background_tasks: BackgroundTasks,
    body: bytes = Depends(verify_slack_signature),
):
    form = dict(parse_qsl(body.decode()))
    payload = json.loads(form.get("payload", "{}"))
    action = (payload.get("actions") or [{}])[0]
    action_id = action.get("action_id")
    response_url = payload.get("response_url", "")
    channel = payload.get("channel", {}).get("id", "")

    if action_id == "approve_ticket":
        ticket = json.loads(action.get("value", "{}"))
        background_tasks.add_task(_create_thread_ticket_and_notify, channel, ticket, response_url)
        return {"replace_original": True, "text": "Creating Jira ticket..."}

    if action_id == "cancel_ticket":
        return {"replace_original": True, "text": "Cancelled."}

    if action_id in ("agent_approve", "agent_reject"):
        run_id = int(action.get("value", "0"))
        background_tasks.add_task(resume_after_approval, run_id, action_id == "agent_approve")
        verb = "Approved" if action_id == "agent_approve" else "Rejected"
        return {"replace_original": True, "text": f"{verb} — resuming agent run..."}

    return {"status": "ignored"}


async def _create_issue_and_notify(
    channel: str, summary: str, requester: str, response_url: str
) -> None:
    try:
        async with SessionLocal() as db:
            project_key = await jira_project_for_channel(db, channel)
        issue = await jira_client.create_issue(
            summary=summary, description=summary, project_key=project_key
        )
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
        logger.exception("Failed to create Jira issue for: %s", summary)
        payload = {
            "response_type": "ephemeral",
            "text": f"❌ Failed to create Jira issue for: {summary}",
        }

    async with httpx.AsyncClient() as client:
        await client.post(response_url, json=payload)


AGENT_SYSTEM_PROMPT = (
    "You are DevFlow's autonomous delivery agent. You have one goal, scoped to one Jira "
    "ticket. Use the available tools to gather information, take action, and decide when "
    "the goal is complete. Call exactly one tool per turn. Some tools require human "
    "approval before they take effect — call them anyway when you decide they're needed; "
    "approval is handled outside your control, and you'll see the result as an observation "
    "on your next turn. When the goal is complete (or you determine it cannot be "
    "completed), call the `finish` tool with a summary and outcome. Don't call `finish` "
    "until you've actually gathered enough information to say something concrete."
)


async def _start_agent_run(channel: str, goal_text: str, ticket_key: str, requester: str) -> None:
    async with SessionLocal() as db:
        run = AgentRun(
            goal=goal_text,
            ticket_key=ticket_key,
            status="planning",
            requested_by=requester,
            channel=channel,
            messages=[
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": f"Goal: {goal_text}\nTicket: {ticket_key}"},
            ],
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id

    await run_agent(run_id)


@router.post("/commands")
async def handle_slack_command(
    background_tasks: BackgroundTasks,
    body: bytes = Depends(verify_slack_signature),
):
    form = dict(parse_qsl(body.decode()))
    text = form.get("text", "").strip()
    response_url = form.get("response_url", "")
    requester = form.get("user_name", "someone")
    channel = form.get("channel_id", "")

    parts = text.split(maxsplit=1)

    if len(parts) >= 2 and parts[0] == "create":
        summary = parts[1].strip()
        background_tasks.add_task(_create_issue_and_notify, channel, summary, requester, response_url)
        return {"response_type": "ephemeral", "text": f"Creating Jira issue for: {summary}..."}

    if len(parts) >= 2 and parts[0] == "agent":
        goal_text = parts[1].strip()
        ticket_key = extract_ticket_key(goal_text)
        if not ticket_key:
            return {
                "response_type": "ephemeral",
                "text": "Usage: /devflow agent <goal mentioning a ticket key>, e.g. "
                "\"take FEAT-2445 through the post-merge workflow\"",
            }
        background_tasks.add_task(_start_agent_run, channel, goal_text, ticket_key, requester)
        return {"response_type": "ephemeral", "text": f"Starting agent run for {ticket_key}..."}

    return {
        "response_type": "ephemeral",
        "text": "Usage: /devflow create <summary>  |  /devflow agent <goal mentioning a ticket key>",
    }
