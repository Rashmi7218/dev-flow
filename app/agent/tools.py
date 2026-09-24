from sqlalchemy import select

from app.db import SessionLocal
from app.integrations import github_client, jira_client, slack_client
from app.models import AgentRun, PullRequest, WorkflowRun

REQUIRES_APPROVAL = {"transition_jira_status"}


async def _get_jira_issue(run: AgentRun) -> dict:
    issue = await jira_client.get_issue(run.ticket_key)
    fields = issue["fields"]
    return {"key": run.ticket_key, "status": fields["status"]["name"], "summary": fields["summary"]}


async def _get_pull_requests_for_ticket(run: AgentRun) -> dict:
    async with SessionLocal() as db:
        result = await db.execute(
            select(PullRequest)
            .where(PullRequest.ticket_key == run.ticket_key)
            .order_by(PullRequest.updated_at)
        )
        prs = result.scalars().all()
    return {
        "pull_requests": [
            {"repo": pr.repo, "number": pr.number, "title": pr.title, "status": pr.status, "url": pr.url}
            for pr in prs
        ]
    }


async def _get_workflow_runs_for_ticket(run: AgentRun) -> dict:
    async with SessionLocal() as db:
        result = await db.execute(
            select(WorkflowRun)
            .where(WorkflowRun.ticket_key == run.ticket_key)
            .order_by(WorkflowRun.updated_at)
        )
        runs = result.scalars().all()
    return {
        "workflow_runs": [
            {
                "repo": r.repo,
                "run_id": r.run_id,
                "name": r.name,
                "status": r.status,
                "conclusion": r.conclusion,
            }
            for r in runs
        ]
    }


async def _get_workflow_run_jobs(run: AgentRun, repo: str, run_id: int) -> dict:
    jobs = await github_client.get_workflow_run_jobs(repo, run_id)
    return {
        "jobs": [
            {
                "name": job["name"],
                "conclusion": job.get("conclusion"),
                "steps": [
                    {"name": s["name"], "conclusion": s.get("conclusion")}
                    for s in job.get("steps", [])
                ],
            }
            for job in jobs
        ]
    }


async def _transition_jira_status(run: AgentRun, status_name: str) -> dict:
    transitions = await jira_client.get_transitions(run.ticket_key)
    match = next((t for t in transitions if t["name"].lower() == status_name.lower()), None)
    if not match:
        available = [t["name"] for t in transitions]
        return {"error": f"No transition named '{status_name}'. Available: {available}"}
    await jira_client.transition_issue(run.ticket_key, match["id"])
    return {"transitioned_to": match["name"]}


async def _post_slack_message(run: AgentRun, text: str) -> dict:
    await slack_client.post_message(text, channel=run.channel)
    return {"posted": True}


async def _finish(run: AgentRun, summary: str, outcome: str) -> dict:
    return {"summary": summary, "outcome": outcome}


TOOLS = {
    "get_jira_issue": _get_jira_issue,
    "get_pull_requests_for_ticket": _get_pull_requests_for_ticket,
    "get_workflow_runs_for_ticket": _get_workflow_runs_for_ticket,
    "get_workflow_run_jobs": _get_workflow_run_jobs,
    "transition_jira_status": _transition_jira_status,
    "post_slack_message": _post_slack_message,
    "finish": _finish,
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_jira_issue",
            "description": "Get the current status and summary of this run's Jira ticket.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pull_requests_for_ticket",
            "description": "List pull requests DevFlow has seen linked to this run's ticket, "
            "with their repo, number, title, status (opened/merged/closed), and URL.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_workflow_runs_for_ticket",
            "description": "List CI workflow runs DevFlow has seen linked to this run's ticket, "
            "with their repo, run_id, name, status, and conclusion.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_workflow_run_jobs",
            "description": "Get job/step-level detail for a specific CI workflow run — use this "
            "to find out why a run failed. Requires the repo and run_id from "
            "get_workflow_runs_for_ticket.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "e.g. acme/widgets"},
                    "run_id": {"type": "integer"},
                },
                "required": ["repo", "run_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transition_jira_status",
            "description": "Transition this run's Jira ticket to a new status (e.g. 'Done', "
            "'In Review'). Requires human approval before it actually executes.",
            "parameters": {
                "type": "object",
                "properties": {"status_name": {"type": "string"}},
                "required": ["status_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "post_slack_message",
            "description": "Post a message to the Slack channel this run was started from.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Call this when the goal is complete (or can't be completed) to end "
            "the run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "What happened, for a human to read"},
                    "outcome": {"type": "string", "enum": ["success", "failure", "needs_human"]},
                },
                "required": ["summary", "outcome"],
            },
        },
    },
]
