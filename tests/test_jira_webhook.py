import json

import respx
from httpx import Response

from app.config import settings


def _issue_payload(status: str = "In Progress", status_changed: bool = True) -> dict:
    payload = {
        "webhookEvent": "jira:issue_updated",
        "issue": {
            "key": "KAN-1",
            "fields": {
                "summary": "Add login",
                "status": {"name": status},
                "project": {"key": "KAN"},
            },
        },
    }
    if status_changed:
        payload["changelog"] = {"items": [{"field": "status"}]}
    return payload


async def test_invalid_token_rejected(client):
    resp = await client.post(
        "/webhooks/jira?token=wrong-token", content=json.dumps(_issue_payload())
    )
    assert resp.status_code == 401


async def test_missing_issue_is_ignored(client):
    resp = await client.post(
        f"/webhooks/jira?token={settings.jira_webhook_token}",
        content=json.dumps({"webhookEvent": "jira:something"}),
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored"}


@respx.mock
async def test_status_change_posts_to_slack(client):
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    resp = await client.post(
        f"/webhooks/jira?token={settings.jira_webhook_token}",
        content=json.dumps(_issue_payload(status="In Progress")),
    )

    assert resp.status_code == 200
    assert slack_route.called
    sent_text = json.loads(slack_route.calls.last.request.content)["text"]
    assert "KAN-1" in sent_text
    assert "In Progress" in sent_text


@respx.mock
async def test_non_status_change_does_not_post_to_slack(client):
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    resp = await client.post(
        f"/webhooks/jira?token={settings.jira_webhook_token}",
        content=json.dumps(_issue_payload(status_changed=False)),
    )

    assert resp.status_code == 200
    assert not slack_route.called
