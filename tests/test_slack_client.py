import pytest
import respx
from httpx import Response

from app.integrations import slack_client


@respx.mock
async def test_post_message_sends_channel_thread_and_blocks():
    route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    await slack_client.post_message(
        "hi", channel="C1", thread_ts="123.456", blocks=[{"type": "section"}]
    )

    sent = route.calls.last.request
    import json

    body = json.loads(sent.content)
    assert body["channel"] == "C1"
    assert body["thread_ts"] == "123.456"
    assert body["blocks"] == [{"type": "section"}]


@respx.mock
async def test_post_message_defaults_to_configured_channel():
    route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    await slack_client.post_message("hi")

    import json

    body = json.loads(route.calls.last.request.content)
    assert body["channel"] == slack_client.settings.slack_default_channel
    assert "thread_ts" not in body
    assert "blocks" not in body


@respx.mock
async def test_post_message_raises_on_slack_error():
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": False, "error": "channel_not_found"})
    )

    with pytest.raises(RuntimeError, match="channel_not_found"):
        await slack_client.post_message("hi", channel="C1")


@respx.mock
async def test_get_thread_replies_raises_on_slack_error():
    respx.get("https://slack.com/api/conversations.replies").mock(
        return_value=Response(200, json={"ok": False, "error": "not_in_channel"})
    )

    with pytest.raises(RuntimeError, match="not_in_channel"):
        await slack_client.get_thread_replies("C1", "123.456")


@respx.mock
async def test_get_thread_replies_returns_messages():
    respx.get("https://slack.com/api/conversations.replies").mock(
        return_value=Response(200, json={"ok": True, "messages": [{"text": "hi"}]})
    )

    messages = await slack_client.get_thread_replies("C1", "123.456")

    assert messages == [{"text": "hi"}]


@respx.mock
async def test_get_permalink_raises_on_slack_error():
    respx.get("https://slack.com/api/chat.getPermalink").mock(
        return_value=Response(200, json={"ok": False, "error": "message_not_found"})
    )

    with pytest.raises(RuntimeError, match="message_not_found"):
        await slack_client.get_permalink("C1", "123.456")


@respx.mock
async def test_get_permalink_returns_url():
    respx.get("https://slack.com/api/chat.getPermalink").mock(
        return_value=Response(200, json={"ok": True, "permalink": "https://slack.com/p1"})
    )

    url = await slack_client.get_permalink("C1", "123.456")

    assert url == "https://slack.com/p1"
