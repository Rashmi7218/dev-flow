import respx
from httpx import Response

from app.db import SessionLocal
from app.models import Issue, PullRequest, WorkflowRun


async def _seed(*objs):
    async with SessionLocal() as db:
        db.add_all(objs)
        await db.commit()


async def test_dashboard_page_serves_html(client):
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "DevFlow Dashboard" in resp.text


async def test_events_endpoint_empty(client):
    resp = await client.get("/api/events")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_events_endpoint_orders_newest_first(client):
    from app.models import Event

    await _seed(
        Event(source="github", event_type="pull_request.opened", payload={}, ticket_key="KAN-1"),
        Event(source="jira", event_type="jira:issue_updated", payload={}, ticket_key="KAN-1"),
    )

    resp = await client.get("/api/events")
    events = resp.json()

    assert len(events) == 2
    assert events[0]["event_type"] == "jira:issue_updated"
    assert events[1]["event_type"] == "pull_request.opened"


async def test_events_endpoint_respects_limit(client):
    from app.models import Event

    await _seed(*[Event(source="github", event_type="x", payload={}) for _ in range(5)])

    resp = await client.get("/api/events?limit=2")

    assert len(resp.json()) == 2


async def test_ticket_timeline_404_when_nothing_found(client):
    resp = await client.get("/api/tickets/NOPE-1/timeline")
    assert resp.status_code == 404


@respx.mock
async def test_ticket_timeline_combines_and_sorts_pr_and_workflow_data(client):
    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-1").mock(
        return_value=Response(
            200, json={"fields": {"status": {"name": "In Progress"}, "summary": "Add login"}}
        )
    )
    await _seed(
        PullRequest(
            repo="acme/widgets",
            number=1,
            title="Add login",
            url="https://github.com/acme/widgets/pull/1",
            author="octocat",
            status="opened",
            ticket_key="KAN-1",
        ),
        WorkflowRun(
            repo="acme/widgets",
            run_id=123,
            name="CI",
            status="completed",
            conclusion="success",
            ticket_key="KAN-1",
        ),
    )

    resp = await client.get("/api/tickets/kan-1/timeline")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ticket_key"] == "KAN-1"
    assert data["jira_status"] == "In Progress"
    assert len(data["timeline"]) == 2
    kinds = [item["kind"] for item in data["timeline"]]
    assert set(kinds) == {"pull_request", "workflow_run"}


@respx.mock
async def test_ticket_timeline_falls_back_to_local_issue_when_jira_unreachable(client):
    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-2").mock(return_value=Response(500))
    await _seed(Issue(key="KAN-2", project="KAN", summary="Cached summary", status="Done"))

    resp = await client.get("/api/tickets/KAN-2/timeline")

    assert resp.status_code == 200
    data = resp.json()
    assert data["jira_status"] == "Done"
    assert data["jira_summary"] == "Cached summary"
