import hashlib
import hmac
import json

import respx
from httpx import Response

from app.config import settings
from app.db import SessionLocal
from app.models import ChannelBinding


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()


def _mock_github_app_auth():
    respx.get("https://api.github.com/repos/acme/widgets/installation").mock(
        return_value=Response(200, json={"id": 999})
    )
    respx.post("https://api.github.com/app/installations/999/access_tokens").mock(
        return_value=Response(
            201, json={"token": "test-installation-token", "expires_at": "2099-01-01T00:00:00Z"}
        )
    )


def _pr_payload(action: str, merged: bool = False) -> dict:
    return {
        "action": action,
        "repository": {"full_name": "acme/widgets"},
        "pull_request": {
            "number": 42,
            "title": "KAN-1 add login",
            "body": "",
            "html_url": "https://github.com/acme/widgets/pull/42",
            "merged": merged,
            "user": {"login": "octocat"},
            "head": {"ref": "feature/KAN-1-login"},
        },
    }


async def test_missing_signature_rejected(client):
    resp = await client.post("/webhooks/github", content=b"{}")
    assert resp.status_code == 401


@respx.mock
async def test_duplicate_delivery_id_is_not_reprocessed(client):
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = json.dumps(_pr_payload("opened")).encode()
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": _sign(body),
        "X-GitHub-Delivery": "same-delivery-id",
    }

    first = await client.post("/webhooks/github", content=body, headers=headers)
    second = await client.post("/webhooks/github", content=body, headers=headers)

    assert first.status_code == 200
    assert first.json() == {"status": "accepted"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert slack_route.call_count == 1


@respx.mock
async def test_different_delivery_ids_both_processed(client):
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = json.dumps(_pr_payload("opened")).encode()
    headers_1 = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": _sign(body),
        "X-GitHub-Delivery": "delivery-1",
    }
    headers_2 = {**headers_1, "X-GitHub-Delivery": "delivery-2"}

    await client.post("/webhooks/github", content=body, headers=headers_1)
    await client.post("/webhooks/github", content=body, headers=headers_2)

    assert slack_route.call_count == 2


async def test_invalid_signature_rejected(client):
    body = json.dumps(_pr_payload("opened")).encode()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert resp.status_code == 401


@respx.mock
async def test_pull_request_opened_posts_summary_to_slack(client):
    _mock_github_app_auth()
    respx.get("https://api.github.com/repos/acme/widgets/pulls/42/files").mock(
        return_value=Response(200, json=[{"filename": "a.py", "additions": 5, "deletions": 1}])
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=Response(
            200,
            json={"choices": [{"message": {"content": "Adds a login button."}}]},
        )
    )
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = json.dumps(_pr_payload("opened")).encode()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body)},
    )

    assert resp.status_code == 200
    assert slack_route.called
    sent_text = json.loads(slack_route.calls.last.request.content)["text"]
    assert "PR Opened" in sent_text
    assert "Adds a login button." in sent_text


@respx.mock
async def test_pull_request_opened_degrades_gracefully_if_groq_fails(client):
    _mock_github_app_auth()
    respx.get("https://api.github.com/repos/acme/widgets/pulls/42/files").mock(
        return_value=Response(200, json=[])
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(return_value=Response(500))
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = json.dumps(_pr_payload("opened")).encode()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body)},
    )

    assert resp.status_code == 200
    sent_text = json.loads(slack_route.calls.last.request.content)["text"]
    assert "PR Opened" in sent_text
    assert "Summary" not in sent_text


@respx.mock
async def test_pull_request_opened_fans_out_to_bound_channels(client):
    async with SessionLocal() as db:
        db.add_all(
            [
                ChannelBinding(repo="acme/widgets", slack_channel="C111"),
                ChannelBinding(repo="acme/widgets", slack_channel="C222"),
            ]
        )
        await db.commit()

    _mock_github_app_auth()
    respx.get("https://api.github.com/repos/acme/widgets/pulls/42/files").mock(
        return_value=Response(200, json=[])
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(return_value=Response(500))
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = json.dumps(_pr_payload("opened")).encode()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body)},
    )

    assert resp.status_code == 200
    assert slack_route.call_count == 2
    sent_channels = {
        json.loads(call.request.content)["channel"] for call in slack_route.calls
    }
    assert sent_channels == {"C111", "C222"}


@respx.mock
async def test_pull_request_merged_posts_to_slack(client):
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = json.dumps(_pr_payload("closed", merged=True)).encode()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body)},
    )

    assert resp.status_code == 200
    sent_text = json.loads(slack_route.calls.last.request.content)["text"]
    assert "PR Merged" in sent_text


def _workflow_run_payload(conclusion: str) -> dict:
    return {
        "action": "completed",
        "repository": {"full_name": "acme/widgets"},
        "workflow_run": {
            "id": 999,
            "name": "CI",
            "status": "completed",
            "conclusion": conclusion,
            "html_url": "https://github.com/acme/widgets/actions/runs/999",
            "head_branch": "feature/KAN-1-login",
            "display_title": "KAN-1 add login",
        },
    }


@respx.mock
async def test_workflow_run_failure_includes_explanation(client):
    _mock_github_app_auth()
    respx.get("https://api.github.com/repos/acme/widgets/actions/runs/999/jobs").mock(
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
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "Tests failed.",
                                    "likely_cause": "A test assertion failed.",
                                    "failed_stage": "Run tests",
                                    "suggested_action": "Check the test output.",
                                }
                            )
                        }
                    }
                ]
            },
        )
    )
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = json.dumps(_workflow_run_payload("failure")).encode()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "workflow_run", "X-Hub-Signature-256": _sign(body)},
    )

    assert resp.status_code == 200
    sent_text = json.loads(slack_route.calls.last.request.content)["text"]
    assert "Failed" in sent_text
    assert "Run tests" in sent_text


@respx.mock
async def test_workflow_run_success_has_no_explanation_call(client):
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = json.dumps(_workflow_run_payload("success")).encode()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "workflow_run", "X-Hub-Signature-256": _sign(body)},
    )

    assert resp.status_code == 200
    sent_text = json.loads(slack_route.calls.last.request.content)["text"]
    assert "Succeeded" in sent_text
