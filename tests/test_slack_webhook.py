import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import respx
from httpx import Response
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import AgentRun, ChannelBinding, RepoConfig


def _slack_headers(body: bytes, timestamp: int | None = None) -> dict:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    basestring = f"v0:{ts}:{body.decode()}".encode()
    signature = "v0=" + hmac.new(
        settings.slack_signing_secret.encode(), basestring, hashlib.sha256
    ).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": signature}


async def test_invalid_signature_rejected_on_commands(client):
    body = urlencode({"text": "create something", "response_url": "https://x"}).encode()
    resp = await client.post(
        "/webhooks/slack/commands",
        content=body,
        headers={"X-Slack-Request-Timestamp": "0", "X-Slack-Signature": "v0=bad"},
    )
    assert resp.status_code == 401


async def test_stale_timestamp_rejected_even_with_valid_signature(client):
    body = urlencode({"text": "create something", "response_url": "https://x"}).encode()
    old_timestamp = int(time.time()) - 600  # 10 minutes old, signature computed to match
    resp = await client.post(
        "/webhooks/slack/commands",
        content=body,
        headers=_slack_headers(body, timestamp=old_timestamp),
    )
    assert resp.status_code == 401


async def test_url_verification_challenge_echoed(client):
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    resp = await client.post(
        "/webhooks/slack/events", content=body, headers=_slack_headers(body)
    )
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "abc123"}


async def test_command_without_create_prefix_returns_usage(client):
    body = urlencode({"text": "delete stuff", "response_url": "https://x"}).encode()
    resp = await client.post(
        "/webhooks/slack/commands", content=body, headers=_slack_headers(body)
    )
    assert resp.status_code == 200
    assert "Usage" in resp.json()["text"]


async def test_command_agent_without_ticket_key_returns_usage(client):
    body = urlencode(
        {"text": "agent take something through the post-merge workflow", "response_url": "https://x"}
    ).encode()
    resp = await client.post(
        "/webhooks/slack/commands", content=body, headers=_slack_headers(body)
    )
    assert resp.status_code == 200
    assert "Usage" in resp.json()["text"]


@respx.mock
async def test_command_agent_starts_a_run_and_acks_immediately(client):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "finish",
                                        "arguments": json.dumps(
                                            {"summary": "Done", "outcome": "success"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )
    )
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = urlencode(
        {
            "text": "agent take KAN-1 through the post-merge workflow",
            "response_url": "https://x",
            "user_name": "alice",
            "channel_id": "C123",
        }
    ).encode()
    resp = await client.post(
        "/webhooks/slack/commands", content=body, headers=_slack_headers(body)
    )

    assert resp.status_code == 200
    assert "Starting agent run for KAN-1" in resp.json()["text"]

    async with SessionLocal() as db:
        result = await db.execute(select(AgentRun).where(AgentRun.ticket_key == "KAN-1"))
        run = result.scalar_one()
    assert run.status == "done"
    assert run.final_summary == "Done"


async def test_command_comment_without_text_returns_usage(client):
    body = urlencode({"text": "comment KAN-1", "response_url": "https://x"}).encode()
    resp = await client.post(
        "/webhooks/slack/commands", content=body, headers=_slack_headers(body)
    )
    assert resp.status_code == 200
    assert "Usage: /devflow comment" in resp.json()["text"]


@respx.mock
async def test_command_comment_adds_comment_and_acks_immediately(client):
    comment_route = respx.post(
        "https://test.atlassian.net/rest/api/3/issue/KAN-1/comment"
    ).mock(return_value=Response(200, json={"id": "1"}))
    response_url_route = respx.post("https://hooks.slack.test/reply").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = urlencode(
        {
            "text": "comment kan-1 this is blocked on infra",
            "response_url": "https://hooks.slack.test/reply",
        }
    ).encode()
    resp = await client.post(
        "/webhooks/slack/commands", content=body, headers=_slack_headers(body)
    )

    assert resp.status_code == 200
    assert "Adding comment to KAN-1" in resp.json()["text"]
    assert comment_route.called
    assert response_url_route.called
    sent = json.loads(response_url_route.calls.last.request.content)
    assert "KAN-1" in sent["text"]


async def test_command_describe_without_text_returns_usage(client):
    body = urlencode({"text": "describe KAN-1", "response_url": "https://x"}).encode()
    resp = await client.post(
        "/webhooks/slack/commands", content=body, headers=_slack_headers(body)
    )
    assert resp.status_code == 200
    assert "Usage: /devflow describe" in resp.json()["text"]


@respx.mock
async def test_command_describe_appends_to_existing_description(client):
    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-1").mock(
        return_value=Response(200, json={"fields": {"description": None}})
    )
    put_route = respx.put("https://test.atlassian.net/rest/api/3/issue/KAN-1").mock(
        return_value=Response(204)
    )
    response_url_route = respx.post("https://hooks.slack.test/reply").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = urlencode(
        {
            "text": "describe kan-1 Provision managed Postgres and wire up the ORM",
            "response_url": "https://hooks.slack.test/reply",
        }
    ).encode()
    resp = await client.post(
        "/webhooks/slack/commands", content=body, headers=_slack_headers(body)
    )

    assert resp.status_code == 200
    assert "Updating description for KAN-1" in resp.json()["text"]
    assert put_route.called
    assert response_url_route.called
    sent = json.loads(response_url_route.calls.last.request.content)
    assert "KAN-1" in sent["text"]


@respx.mock
async def test_command_create_acks_immediately_and_creates_issue_in_background(client):
    jira_route = respx.post("https://test.atlassian.net/rest/api/3/issue").mock(
        return_value=Response(200, json={"key": "TEST-1"})
    )
    response_url_route = respx.post("https://hooks.slack.test/reply").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = urlencode(
        {
            "text": "create fix the login bug",
            "response_url": "https://hooks.slack.test/reply",
            "user_name": "alice",
        }
    ).encode()
    resp = await client.post(
        "/webhooks/slack/commands", content=body, headers=_slack_headers(body)
    )

    assert resp.status_code == 200
    assert "Creating Jira issue" in resp.json()["text"]
    assert jira_route.called
    assert response_url_route.called
    sent = json.loads(response_url_route.calls.last.request.content)
    assert "TEST-1" in sent["text"]


@respx.mock
async def test_command_create_uses_jira_project_bound_to_channel(client):
    async with SessionLocal() as db:
        db.add(RepoConfig(repo="acme/widgets", jira_project_key="WID"))
        db.add(ChannelBinding(repo="acme/widgets", slack_channel="C999"))
        await db.commit()

    jira_route = respx.post("https://test.atlassian.net/rest/api/3/issue").mock(
        return_value=Response(200, json={"key": "WID-1"})
    )
    respx.post("https://hooks.slack.test/reply").mock(return_value=Response(200, json={"ok": True}))

    body = urlencode(
        {
            "text": "create fix the login bug",
            "response_url": "https://hooks.slack.test/reply",
            "user_name": "alice",
            "channel_id": "C999",
        }
    ).encode()
    resp = await client.post(
        "/webhooks/slack/commands", content=body, headers=_slack_headers(body)
    )

    assert resp.status_code == 200
    assert jira_route.called
    sent_fields = json.loads(jira_route.calls.last.request.content)["fields"]
    assert sent_fields["project"]["key"] == "WID"


@respx.mock
async def test_duplicate_event_id_is_not_reprocessed(client):
    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-4").mock(
        return_value=Response(
            200, json={"fields": {"status": {"name": "In Progress"}, "summary": "Add login"}}
        )
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=Response(
            200, json={"choices": [{"message": {"content": "Still being worked on."}}]}
        )
    )
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev0SAME00",
            "event": {
                "type": "app_mention",
                "text": "<@BOTID> what's up with KAN-4?",
                "channel": "C123",
                "ts": "111.222",
            },
        }
    ).encode()

    first = await client.post(
        "/webhooks/slack/events", content=body, headers=_slack_headers(body)
    )
    second = await client.post(
        "/webhooks/slack/events", content=body, headers=_slack_headers(body)
    )

    assert first.json() == {"status": "accepted"}
    assert second.json() == {"status": "duplicate"}
    assert slack_route.call_count == 1


@respx.mock
@respx.mock
async def test_app_mention_outside_thread_without_ticket_key_lists_all_usage_options(client):
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = json.dumps(
        {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "text": "<@BOTID> create ticket for this task \"Database Provisioning\"",
                "channel": "C123",
                "ts": "111.222",
            },
        }
    ).encode()
    resp = await client.post(
        "/webhooks/slack/events", content=body, headers=_slack_headers(body)
    )

    assert resp.status_code == 200
    sent_text = json.loads(slack_route.calls.last.request.content)["text"]
    assert "/devflow create" in sent_text
    assert "/devflow agent" in sent_text
    assert "/devflow comment" in sent_text
    assert "/devflow describe" in sent_text
    assert "create ticket from this thread" in sent_text


@respx.mock
async def test_app_mention_with_ticket_key_answers_status_query(client):
    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-4").mock(
        return_value=Response(
            200, json={"fields": {"status": {"name": "In Progress"}, "summary": "Add login"}}
        )
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=Response(
            200, json={"choices": [{"message": {"content": "Still being worked on."}}]}
        )
    )
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    body = json.dumps(
        {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "text": "<@BOTID> what's up with KAN-4?",
                "channel": "C123",
                "ts": "111.222",
            },
        }
    ).encode()
    resp = await client.post(
        "/webhooks/slack/events", content=body, headers=_slack_headers(body)
    )

    assert resp.status_code == 200
    assert slack_route.called
    sent_text = json.loads(slack_route.calls.last.request.content)["text"]
    assert "KAN-4" in sent_text
    assert "In Progress" in sent_text


@respx.mock
async def test_app_mention_in_thread_proposes_ticket(client):
    respx.get("https://slack.com/api/conversations.replies").mock(
        return_value=Response(
            200,
            json={
                "ok": True,
                "messages": [{"user": "U1", "text": "Login is broken with a 500 error"}],
            },
        )
    )
    respx.get("https://slack.com/api/chat.getPermalink").mock(
        return_value=Response(200, json={"ok": True, "permalink": "https://slack.com/p1"})
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
                                    "title": "Login 500 error",
                                    "description": "Login fails.",
                                    "steps_to_reproduce": "Try logging in.",
                                    "expected_behavior": "Login succeeds.",
                                    "actual_behavior": "500 error.",
                                    "severity": "High",
                                    "priority": "High",
                                    "suggested_assignee": "Backend team",
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

    body = json.dumps(
        {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "text": "<@BOTID> create ticket from this thread",
                "channel": "C123",
                "ts": "111.333",
                "thread_ts": "111.222",
            },
        }
    ).encode()
    resp = await client.post(
        "/webhooks/slack/events", content=body, headers=_slack_headers(body)
    )

    assert resp.status_code == 200
    assert slack_route.called
    sent = json.loads(slack_route.calls.last.request.content)
    assert "approve_ticket" in json.dumps(sent["blocks"])
    assert "Login 500 error" in json.dumps(sent["blocks"])


@respx.mock
async def test_interaction_approve_creates_ticket(client):
    jira_route = respx.post("https://test.atlassian.net/rest/api/3/issue").mock(
        return_value=Response(200, json={"key": "TEST-9"})
    )
    response_url_route = respx.post("https://hooks.slack.test/reply2").mock(
        return_value=Response(200, json={"ok": True})
    )

    ticket = {
        "title": "Login 500 error",
        "description": "Login fails.",
        "steps_to_reproduce": "Try logging in.",
        "expected_behavior": "Login succeeds.",
        "actual_behavior": "500 error.",
        "severity": "High",
        "suggested_assignee": "Backend team",
        "thread_url": "https://slack.com/p1",
    }
    interaction_payload = {
        "type": "block_actions",
        "response_url": "https://hooks.slack.test/reply2",
        "actions": [{"action_id": "approve_ticket", "value": json.dumps(ticket)}],
    }
    body = urlencode({"payload": json.dumps(interaction_payload)}).encode()
    resp = await client.post(
        "/webhooks/slack/interactions", content=body, headers=_slack_headers(body)
    )

    assert resp.status_code == 200
    assert "Creating Jira ticket" in resp.json()["text"]
    assert jira_route.called
    assert response_url_route.called
    sent = json.loads(response_url_route.calls.last.request.content)
    assert "TEST-9" in sent["text"]


@respx.mock
async def test_interaction_approve_uses_jira_project_bound_to_channel(client):
    async with SessionLocal() as db:
        db.add(RepoConfig(repo="acme/widgets", jira_project_key="WID"))
        db.add(ChannelBinding(repo="acme/widgets", slack_channel="C123"))
        await db.commit()

    jira_route = respx.post("https://test.atlassian.net/rest/api/3/issue").mock(
        return_value=Response(200, json={"key": "WID-2"})
    )
    respx.post("https://hooks.slack.test/reply3").mock(return_value=Response(200, json={"ok": True}))

    ticket = {
        "title": "Login 500 error",
        "description": "Login fails.",
        "steps_to_reproduce": "Try logging in.",
        "expected_behavior": "Login succeeds.",
        "actual_behavior": "500 error.",
        "severity": "High",
        "suggested_assignee": "Backend team",
        "thread_url": "https://slack.com/p1",
    }
    interaction_payload = {
        "type": "block_actions",
        "response_url": "https://hooks.slack.test/reply3",
        "channel": {"id": "C123"},
        "actions": [{"action_id": "approve_ticket", "value": json.dumps(ticket)}],
    }
    body = urlencode({"payload": json.dumps(interaction_payload)}).encode()
    resp = await client.post(
        "/webhooks/slack/interactions", content=body, headers=_slack_headers(body)
    )

    assert resp.status_code == 200
    assert jira_route.called
    sent_fields = json.loads(jira_route.calls.last.request.content)["fields"]
    assert sent_fields["project"]["key"] == "WID"


async def test_interaction_cancel_replaces_message_without_creating_ticket(client):
    interaction_payload = {
        "type": "block_actions",
        "response_url": "https://hooks.slack.test/unused",
        "actions": [{"action_id": "cancel_ticket"}],
    }
    body = urlencode({"payload": json.dumps(interaction_payload)}).encode()
    resp = await client.post(
        "/webhooks/slack/interactions", content=body, headers=_slack_headers(body)
    )

    assert resp.status_code == 200
    assert resp.json() == {"replace_original": True, "text": "Cancelled."}
