import json

import respx
from httpx import Response
from sqlalchemy import select

from app.agent.loop import _sanitize_assistant_message, resume_after_approval, run_agent
from app.db import SessionLocal
from app.models import AgentRun, AgentStep

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _groq_response(tool_calls: list[dict]) -> Response:
    return Response(200, json={"choices": [{"message": {"role": "assistant", "tool_calls": tool_calls}}]})


def _groq_text_response(content: str = "Sure, I can help with that.") -> Response:
    return Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


async def _create_run(**overrides) -> int:
    defaults = dict(
        goal="take KAN-1 through the post-merge workflow",
        ticket_key="KAN-1",
        status="planning",
        requested_by="alice",
        channel="C123",
        messages=[
            {"role": "system", "content": "You are an agent."},
            {"role": "user", "content": "Goal: take KAN-1 through the post-merge workflow"},
        ],
    )
    defaults.update(overrides)
    async with SessionLocal() as db:
        run = AgentRun(**defaults)
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return run.id


async def _get_run(run_id: int) -> AgentRun:
    async with SessionLocal() as db:
        return await db.get(AgentRun, run_id)


async def _get_steps(run_id: int) -> list[AgentStep]:
    async with SessionLocal() as db:
        result = await db.execute(
            select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.step_number)
        )
        return list(result.scalars().all())


def test_sanitize_assistant_message_strips_extra_fields_and_null_content():
    # Shaped like a real provider response: extra response-only fields, null content,
    # and an extra "index" key inside the tool_call itself (both seen from real vendors).
    noisy = {
        "role": "assistant",
        "content": None,
        "reasoning": "thinking about it...",
        "annotations": [],
        "tool_calls": [
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "finish", "arguments": '{"summary": "x"}'},
            }
        ],
    }

    clean = _sanitize_assistant_message(noisy)

    assert clean == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "finish", "arguments": '{"summary": "x"}'},
            }
        ],
    }


@respx.mock
async def test_noisy_model_response_is_sanitized_before_being_replayed():
    noisy_message = {
        "role": "assistant",
        "content": None,
        "reasoning": "internal thinking that shouldn't be replayed",
        "tool_calls": [
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "get_pull_requests_for_ticket",
                    "arguments": "{}",
                },
            }
        ],
    }
    groq_route = respx.post(GROQ_URL).mock(
        side_effect=[
            Response(200, json={"choices": [{"message": noisy_message}]}),
            _groq_response([_tool_call("finish", {"summary": "All good", "outcome": "success"})]),
        ]
    )
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    run_id = await _create_run()
    await run_agent(run_id)

    run = await _get_run(run_id)
    assert run.status == "done"

    # The second request sent to Groq must carry the *sanitized* first turn, not the raw
    # noisy one — otherwise this is the exact 400 seen in production.
    second_request_body = json.loads(groq_route.calls[1].request.content)
    replayed_first_turn = second_request_body["messages"][2]
    assert replayed_first_turn["content"] == ""
    assert "reasoning" not in replayed_first_turn
    assert replayed_first_turn["tool_calls"][0] == {
        "id": "call_1",
        "type": "function",
        "function": {"name": "get_pull_requests_for_ticket", "arguments": "{}"},
    }


@respx.mock
async def test_multi_turn_happy_path_reaches_done():
    respx.post(GROQ_URL).mock(
        side_effect=[
            _groq_response([_tool_call("get_pull_requests_for_ticket", {})]),
            _groq_response([_tool_call("get_workflow_runs_for_ticket", {})]),
            _groq_response([_tool_call("finish", {"summary": "All good", "outcome": "success"})]),
        ]
    )
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    run_id = await _create_run()
    await run_agent(run_id)

    run = await _get_run(run_id)
    assert run.status == "done"
    assert run.final_summary == "All good"

    steps = await _get_steps(run_id)
    assert [s.kind for s in steps] == ["tool_call", "tool_call", "final"]
    assert slack_route.called


@respx.mock
async def test_model_response_without_tool_call_is_nudged_and_retried():
    groq_route = respx.post(GROQ_URL).mock(
        side_effect=[
            _groq_text_response(),
            _groq_response([_tool_call("finish", {"summary": "All good", "outcome": "success"})]),
        ]
    )
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    run_id = await _create_run()
    await run_agent(run_id)

    run = await _get_run(run_id)
    assert run.status == "done"
    assert run.final_summary == "All good"
    assert groq_route.call_count == 2

    # The retry nudge should be visible in the persisted conversation, not silently dropped.
    assert any(
        m.get("role") == "user" and "Call a tool now" in m.get("content", "")
        for m in run.messages
    )


@respx.mock
async def test_model_response_without_tool_call_twice_fails_cleanly():
    respx.post(GROQ_URL).mock(side_effect=[_groq_text_response(), _groq_text_response()])
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    run_id = await _create_run()
    await run_agent(run_id)  # would raise StopIteration if it tried a third Groq call

    run = await _get_run(run_id)
    assert run.status == "failed"
    assert slack_route.called


@respx.mock
async def test_approval_gated_tool_suspends_and_prompts_slack():
    respx.post(GROQ_URL).mock(
        side_effect=[_groq_response([_tool_call("transition_jira_status", {"status_name": "Done"})])]
    )
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    run_id = await _create_run()
    await run_agent(run_id)

    run = await _get_run(run_id)
    assert run.status == "waiting_approval"
    assert run.pending_tool_call["name"] == "transition_jira_status"

    steps = await _get_steps(run_id)
    assert steps[-1].kind == "approval_requested"

    sent = json.loads(slack_route.calls.last.request.content)
    action_ids = {el["action_id"] for el in sent["blocks"][1]["elements"]}
    assert action_ids == {"agent_approve", "agent_reject"}


@respx.mock
async def test_approving_executes_the_tool_and_resumes_to_done():
    respx.post(GROQ_URL).mock(
        side_effect=[_groq_response([_tool_call("transition_jira_status", {"status_name": "Done"})])]
    )
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )
    run_id = await _create_run()
    await run_agent(run_id)

    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-1/transitions").mock(
        return_value=Response(200, json={"transitions": [{"id": "31", "name": "Done"}]})
    )
    respx.post("https://test.atlassian.net/rest/api/3/issue/KAN-1/transitions").mock(
        return_value=Response(204)
    )
    respx.post(GROQ_URL).mock(
        side_effect=[_groq_response([_tool_call("finish", {"summary": "Transitioned", "outcome": "success"})])]
    )

    await resume_after_approval(run_id, approved=True)

    run = await _get_run(run_id)
    assert run.status == "done"
    assert run.final_summary == "Transitioned"
    assert run.pending_tool_call is None

    steps = await _get_steps(run_id)
    assert steps[1].kind == "approval_result"
    assert "Approved" in steps[1].detail
    assert "Done" in steps[1].detail


@respx.mock
async def test_rejecting_skips_the_tool_and_model_replans_around_it():
    respx.post(GROQ_URL).mock(
        side_effect=[_groq_response([_tool_call("transition_jira_status", {"status_name": "Done"})])]
    )
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )
    run_id = await _create_run()
    await run_agent(run_id)

    jira_transitions_route = respx.get(
        "https://test.atlassian.net/rest/api/3/issue/KAN-1/transitions"
    ).mock(return_value=Response(200, json={"transitions": []}))
    respx.post(GROQ_URL).mock(
        side_effect=[
            _groq_response([_tool_call("finish", {"summary": "Skipped per rejection", "outcome": "needs_human"})])
        ]
    )

    await resume_after_approval(run_id, approved=False)

    run = await _get_run(run_id)
    assert run.status == "done"
    assert run.final_summary == "Skipped per rejection"
    assert not jira_transitions_route.called  # rejected tool must not actually execute

    steps = await _get_steps(run_id)
    assert steps[1].kind == "approval_result"
    assert "Rejected" in steps[1].detail


@respx.mock
async def test_exhausting_max_iterations_marks_run_failed_not_infinite():
    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-1").mock(
        return_value=Response(
            200, json={"fields": {"status": {"name": "Open"}, "summary": "s"}}
        )
    )
    respx.post(GROQ_URL).mock(
        side_effect=[_groq_response([_tool_call("get_jira_issue", {})]) for _ in range(8)]
    )
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )

    run_id = await _create_run()
    await run_agent(run_id)  # would raise StopIteration from the mock if it looped past 8 calls

    run = await _get_run(run_id)
    assert run.status == "failed"
