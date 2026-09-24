import json
import logging

from sqlalchemy import func, select

from app.agent.tools import REQUIRES_APPROVAL, TOOL_SCHEMAS, TOOLS
from app.db import SessionLocal
from app.integrations import groq_client, slack_client
from app.models import AgentRun, AgentStep

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 8


async def _next_step_number(db, run_id: int) -> int:
    result = await db.execute(
        select(func.count()).select_from(AgentStep).where(AgentStep.run_id == run_id)
    )
    return (result.scalar_one() or 0) + 1


async def _fail(db, run: AgentRun, reason: str) -> None:
    run.status = "failed"
    step_number = await _next_step_number(db, run.id)
    db.add(AgentStep(run_id=run.id, step_number=step_number, kind="final", detail=reason))
    await db.commit()
    try:
        await slack_client.post_message(
            f"⚠️ Agent run for {run.ticket_key}: {reason}", channel=run.channel
        )
    except Exception:
        logger.exception("Failed to notify Slack about failed agent run %s", run.id)


async def _post_approval_prompt(run: AgentRun, tool_name: str, args: dict) -> None:
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Agent run for {run.ticket_key} wants approval*\n\n"
                    f"Action: `{tool_name}({json.dumps(args)})`"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve"},
                    "style": "primary",
                    "action_id": "agent_approve",
                    "value": str(run.id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Reject"},
                    "style": "danger",
                    "action_id": "agent_reject",
                    "value": str(run.id),
                },
            ],
        },
    ]
    await slack_client.post_message(
        f"Agent run for {run.ticket_key} needs approval to: {tool_name}",
        channel=run.channel,
        blocks=blocks,
    )


async def run_agent(run_id: int) -> None:
    async with SessionLocal() as db:
        run = await db.get(AgentRun, run_id)
        if run is None or run.status not in ("planning", "executing"):
            return

        run.status = "executing"
        await db.commit()

        step_number = await _next_step_number(db, run.id)

        for _ in range(MAX_ITERATIONS):
            try:
                message = await groq_client.agent_step(run.messages, TOOL_SCHEMAS)
            except Exception:
                logger.exception("Agent step failed for run %s", run.id)
                await _fail(db, run, "Failed to reach the AI model.")
                return

            run.messages = run.messages + [message]

            tool_calls = message.get("tool_calls")
            if not tool_calls:
                await _fail(db, run, "Model did not call a tool; stopping.")
                return

            call = tool_calls[0]
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "finish":
                run.final_summary = args.get("summary", "")
                run.status = "done"
                db.add(
                    AgentStep(
                        run_id=run.id,
                        step_number=step_number,
                        kind="final",
                        tool_name="finish",
                        detail=run.final_summary,
                    )
                )
                await db.commit()
                await slack_client.post_message(
                    f"✅ Agent run for {run.ticket_key}: {run.final_summary}", channel=run.channel
                )
                return

            if name in REQUIRES_APPROVAL:
                run.pending_tool_call = {"id": call["id"], "name": name, "args": args}
                run.status = "waiting_approval"
                db.add(
                    AgentStep(
                        run_id=run.id,
                        step_number=step_number,
                        kind="approval_requested",
                        tool_name=name,
                        detail=f"Requesting approval to call {name}({json.dumps(args)})",
                    )
                )
                await db.commit()
                await _post_approval_prompt(run, name, args)
                return

            if name not in TOOLS:
                result = {"error": f"Unknown tool {name}"}
            else:
                try:
                    result = await TOOLS[name](run, **args)
                except Exception as exc:
                    logger.exception("Tool %s failed for run %s", name, run.id)
                    result = {"error": str(exc)}

            db.add(
                AgentStep(
                    run_id=run.id,
                    step_number=step_number,
                    kind="tool_call",
                    tool_name=name,
                    detail=f"{name}({json.dumps(args)}) -> {json.dumps(result)}",
                )
            )
            run.messages = run.messages + [
                {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)}
            ]
            await db.commit()
            step_number += 1

        await _fail(db, run, f"Didn't finish within {MAX_ITERATIONS} steps — needs a human to take over.")


async def resume_after_approval(run_id: int, approved: bool) -> None:
    async with SessionLocal() as db:
        run = await db.get(AgentRun, run_id)
        if run is None or run.status != "waiting_approval" or not run.pending_tool_call:
            return

        pending = run.pending_tool_call
        name = pending["name"]
        args = pending["args"]
        call_id = pending["id"]
        step_number = await _next_step_number(db, run.id)

        if approved:
            try:
                result = await TOOLS[name](run, **args)
                detail = f"Approved: {name}({json.dumps(args)}) -> {json.dumps(result)}"
            except Exception as exc:
                logger.exception("Approved tool %s failed for run %s", name, run.id)
                result = {"error": str(exc)}
                detail = f"Approved but failed: {name}({json.dumps(args)}) -> {exc}"
        else:
            result = {"error": "Human rejected this action."}
            detail = f"Rejected: {name}({json.dumps(args)})"

        db.add(
            AgentStep(
                run_id=run.id,
                step_number=step_number,
                kind="approval_result",
                tool_name=name,
                detail=detail,
            )
        )
        run.messages = run.messages + [
            {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result)}
        ]
        run.pending_tool_call = None
        run.status = "executing"
        await db.commit()

    await run_agent(run_id)
