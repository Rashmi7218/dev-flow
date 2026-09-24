import json

import respx
from httpx import Response

from app.agent.tools import (
    _finish,
    _get_jira_issue,
    _get_pull_requests_for_ticket,
    _get_workflow_run_jobs,
    _get_workflow_runs_for_ticket,
    _post_slack_message,
    _transition_jira_status,
)
from app.db import SessionLocal
from app.models import AgentRun, PullRequest, WorkflowRun


def _run(**overrides) -> AgentRun:
    defaults = dict(
        goal="take KAN-1 through the post-merge workflow",
        ticket_key="KAN-1",
        status="executing",
        requested_by="alice",
        channel="C123",
        messages=[],
    )
    defaults.update(overrides)
    return AgentRun(**defaults)


@respx.mock
async def test_get_jira_issue_returns_status_and_summary():
    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-1").mock(
        return_value=Response(
            200, json={"fields": {"status": {"name": "In Progress"}, "summary": "Add login"}}
        )
    )

    result = await _get_jira_issue(_run())

    assert result == {"key": "KAN-1", "status": "In Progress", "summary": "Add login"}


async def test_get_pull_requests_for_ticket_returns_matching_prs():
    async with SessionLocal() as db:
        db.add(
            PullRequest(
                repo="acme/widgets",
                number=7,
                title="Add login",
                url="https://github.com/acme/widgets/pull/7",
                author="octocat",
                status="merged",
                ticket_key="KAN-1",
            )
        )
        await db.commit()

    result = await _get_pull_requests_for_ticket(_run())

    assert result["pull_requests"] == [
        {
            "repo": "acme/widgets",
            "number": 7,
            "title": "Add login",
            "status": "merged",
            "url": "https://github.com/acme/widgets/pull/7",
        }
    ]


async def test_get_workflow_runs_for_ticket_returns_matching_runs():
    async with SessionLocal() as db:
        db.add(
            WorkflowRun(
                repo="acme/widgets",
                run_id=99,
                name="CI",
                status="completed",
                conclusion="failure",
                ticket_key="KAN-1",
            )
        )
        await db.commit()

    result = await _get_workflow_runs_for_ticket(_run())

    assert result["workflow_runs"] == [
        {"repo": "acme/widgets", "run_id": 99, "name": "CI", "status": "completed", "conclusion": "failure"}
    ]


@respx.mock
async def test_get_workflow_run_jobs_returns_job_and_step_detail():
    respx.get("https://api.github.com/repos/acme/widgets/installation").mock(
        return_value=Response(200, json={"id": 999})
    )
    respx.post("https://api.github.com/app/installations/999/access_tokens").mock(
        return_value=Response(201, json={"token": "t", "expires_at": "2099-01-01T00:00:00Z"})
    )
    respx.get("https://api.github.com/repos/acme/widgets/actions/runs/99/jobs").mock(
        return_value=Response(
            200,
            json={
                "jobs": [
                    {
                        "name": "test",
                        "conclusion": "failure",
                        "steps": [{"name": "Run tests", "conclusion": "failure"}],
                    }
                ]
            },
        )
    )

    result = await _get_workflow_run_jobs(_run(), repo="acme/widgets", run_id=99)

    assert result["jobs"] == [
        {"name": "test", "conclusion": "failure", "steps": [{"name": "Run tests", "conclusion": "failure"}]}
    ]


@respx.mock
async def test_transition_jira_status_matches_by_name_case_insensitive():
    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-1/transitions").mock(
        return_value=Response(
            200, json={"transitions": [{"id": "31", "name": "Done"}, {"id": "11", "name": "In Progress"}]}
        )
    )
    transition_route = respx.post(
        "https://test.atlassian.net/rest/api/3/issue/KAN-1/transitions"
    ).mock(return_value=Response(204))

    result = await _transition_jira_status(_run(), status_name="done")

    assert result == {"transitioned_to": "Done"}
    assert transition_route.called


@respx.mock
async def test_transition_jira_status_reports_unknown_status():
    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-1/transitions").mock(
        return_value=Response(200, json={"transitions": [{"id": "31", "name": "Done"}]})
    )

    result = await _transition_jira_status(_run(), status_name="Blocked")

    assert "error" in result
    assert "Blocked" in result["error"]


@respx.mock
async def test_post_slack_message_uses_run_channel_not_a_model_supplied_one():
    route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    await _post_slack_message(_run(channel="C999"), text="hello")

    body = json.loads(route.calls.last.request.content)
    assert body["channel"] == "C999"
    assert body["text"] == "hello"


async def test_finish_returns_summary_and_outcome():
    result = await _finish(_run(), summary="All done", outcome="success")

    assert result == {"summary": "All done", "outcome": "success"}
