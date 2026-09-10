import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.slack_bot_token}"}


async def post_message(
    text: str,
    channel: str | None = None,
    thread_ts: str | None = None,
    blocks: list[dict] | None = None,
) -> dict:
    payload: dict = {"channel": channel or settings.slack_default_channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if blocks:
        payload["blocks"] = blocks

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage", headers=_headers(), json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API error: {data.get('error')}")
        return data


async def post_to_channels(text: str, channels: list[str]) -> None:
    for channel in channels:
        try:
            await post_message(text, channel=channel)
        except Exception:
            logger.exception("Failed to post to Slack channel %s", channel)


async def get_thread_replies(channel: str, thread_ts: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://slack.com/api/conversations.replies",
            headers=_headers(),
            params={"channel": channel, "ts": thread_ts},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API error: {data.get('error')}")
        return data["messages"]


async def get_permalink(channel: str, message_ts: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://slack.com/api/chat.getPermalink",
            headers=_headers(),
            params={"channel": channel, "message_ts": message_ts},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API error: {data.get('error')}")
        return data["permalink"]
