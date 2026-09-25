import json

import pytest
import respx
from httpx import HTTPStatusError, Response

from app.integrations import groq_client


def _fake_chat(captured: dict, response: str):
    async def _chat(prompt: str, *, max_tokens: int, json_mode: bool = False) -> str:
        captured["prompt"] = prompt
        captured["max_tokens"] = max_tokens
        captured["json_mode"] = json_mode
        return response

    return _chat


async def test_summarize_pr_includes_title_body_and_files(monkeypatch):
    captured = {}
    monkeypatch.setattr(groq_client, "_chat", _fake_chat(captured, "a summary"))

    files = [{"filename": "app/main.py", "additions": 3, "deletions": 1, "patch": "+added line"}]
    result = await groq_client.summarize_pr("Fix login", "Fixes the bug", files)

    assert result == "a summary"
    assert "Fix login" in captured["prompt"]
    assert "Fixes the bug" in captured["prompt"]
    assert "app/main.py" in captured["prompt"]
    assert "+added line" in captured["prompt"]
    assert captured["json_mode"] is False


async def test_summarize_pr_truncates_long_patch(monkeypatch):
    captured = {}
    monkeypatch.setattr(groq_client, "_chat", _fake_chat(captured, "a summary"))

    files = [{"filename": "big.py", "additions": 1, "deletions": 0, "patch": "x" * 5000}]
    await groq_client.summarize_pr("Title", None, files)

    # patch is capped at 1000 chars per file in the prompt
    assert captured["prompt"].count("x") == 1000


async def test_summarize_pr_limits_to_20_files(monkeypatch):
    captured = {}
    monkeypatch.setattr(groq_client, "_chat", _fake_chat(captured, "a summary"))

    files = [
        {"filename": f"file{i}.py", "additions": 1, "deletions": 0} for i in range(30)
    ]
    await groq_client.summarize_pr("Title", None, files)

    assert "file19.py" in captured["prompt"]
    assert "file20.py" not in captured["prompt"]


async def test_summarize_pr_handles_no_files(monkeypatch):
    captured = {}
    monkeypatch.setattr(groq_client, "_chat", _fake_chat(captured, "summary"))

    result = await groq_client.summarize_pr("Title", None, [])

    assert result == "summary"
    assert "No file diffs available." in captured["prompt"]


async def test_explain_failure_skips_successful_jobs_and_steps(monkeypatch):
    captured = {}
    response = json.dumps(
        {
            "summary": "s",
            "likely_cause": "c",
            "failed_stage": "build",
            "suggested_action": "a",
        }
    )
    monkeypatch.setattr(groq_client, "_chat", _fake_chat(captured, response))

    jobs = [
        {"name": "lint", "conclusion": "success", "steps": []},
        {
            "name": "build",
            "conclusion": "failure",
            "steps": [
                {"name": "Checkout", "conclusion": "success"},
                {"name": "Compile", "conclusion": "failure"},
                {"name": "Notify", "conclusion": "skipped"},
            ],
        },
    ]
    result = await groq_client.explain_failure("CI", jobs)

    assert result == {
        "summary": "s",
        "likely_cause": "c",
        "failed_stage": "build",
        "suggested_action": "a",
    }
    assert "lint" not in captured["prompt"]
    assert "Checkout" not in captured["prompt"]
    assert "Notify" not in captured["prompt"]
    assert "Compile" in captured["prompt"]
    assert captured["json_mode"] is True


async def test_explain_failure_handles_no_failed_details(monkeypatch):
    captured = {}
    response = json.dumps(
        {"summary": "s", "likely_cause": "c", "failed_stage": "?", "suggested_action": "a"}
    )
    monkeypatch.setattr(groq_client, "_chat", _fake_chat(captured, response))

    await groq_client.explain_failure("CI", [{"name": "x", "conclusion": "success"}])

    assert "No failed job/step details available." in captured["prompt"]


async def test_extract_ticket_from_thread_builds_transcript(monkeypatch):
    captured = {}
    response = json.dumps(
        {
            "title": "t",
            "description": "d",
            "steps_to_reproduce": "s",
            "expected_behavior": "e",
            "actual_behavior": "a",
            "severity": "High",
            "priority": "High",
            "suggested_assignee": "team",
        }
    )
    monkeypatch.setattr(groq_client, "_chat", _fake_chat(captured, response))

    messages = [
        {"user": "U1", "text": "Login is broken"},
        {"user": "U2", "text": "Confirmed, seeing 500s"},
    ]
    result = await groq_client.extract_ticket_from_thread(messages)

    assert result["title"] == "t"
    assert "U1: Login is broken" in captured["prompt"]
    assert "U2: Confirmed, seeing 500s" in captured["prompt"]
    assert captured["json_mode"] is True


async def test_phrase_status_update_passes_facts_through(monkeypatch):
    captured = {}
    monkeypatch.setattr(groq_client, "_chat", _fake_chat(captured, "It just failed."))

    result = await groq_client.phrase_status_update("Ticket: KAN-1\nStatus: To Do")

    assert result == "It just failed."
    assert "Ticket: KAN-1" in captured["prompt"]
    assert captured["json_mode"] is False


@respx.mock
async def test_agent_step_sends_tools_and_auto_tool_choice():
    # "auto", not "required" — Groq hard-fails the whole request with a 400 when
    # tool_choice="required" and the model replies with plain text instead of a tool
    # call, which bypasses the loop's nudge-and-retry entirely. See the comment on
    # agent_step's tool_choice for the full story (found via a real production failure).
    route = respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "tool_calls": []}}]},
        )
    )

    messages = [{"role": "user", "content": "go"}]
    tools = [{"type": "function", "function": {"name": "finish"}}]
    await groq_client.agent_step(messages, tools)

    sent = json.loads(route.calls.last.request.content)
    assert sent["tools"] == tools
    assert sent["tool_choice"] == "auto"


@respx.mock
async def test_agent_step_error_includes_response_body_for_debuggability():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=Response(
            400, json={"error": {"message": "messages: content field required"}}
        )
    )

    with pytest.raises(HTTPStatusError, match="content field required"):
        await groq_client.agent_step([{"role": "user", "content": "go"}], [])
