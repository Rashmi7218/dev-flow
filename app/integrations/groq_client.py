import json

import httpx

from app.config import settings

BASE_URL = "https://api.groq.com/openai/v1"


async def _chat(prompt: str, *, max_tokens: int, json_mode: bool = False) -> str:
    body = {
        "model": settings.groq_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "reasoning_effort": "low",
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def agent_step(messages: list[dict], tools: list[dict]) -> dict:
    body = {
        "model": settings.groq_model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 800,
        "reasoning_effort": "low",
        "tools": tools,
        "tool_choice": "required",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]


async def summarize_pr(title: str, body: str | None, files: list[dict]) -> str:
    file_lines = []
    for f in files[:20]:
        line = f"- {f['filename']} (+{f['additions']}/-{f['deletions']})"
        patch = f.get("patch")
        if patch:
            line += f"\n{patch[:1000]}"
        file_lines.append(line)
    files_text = "\n".join(file_lines) or "No file diffs available."

    prompt = (
        "Summarize this pull request for an engineering Slack channel in 2-3 sentences. "
        "Be specific about what changed and why it matters — don't just restate the title.\n\n"
        f"Title: {title}\n"
        f"Description: {body or '(none)'}\n\n"
        f"Changed files:\n{files_text}"
    )
    return await _chat(prompt, max_tokens=200)


async def explain_failure(workflow_name: str, jobs: list[dict]) -> dict:
    job_lines = []
    for job in jobs:
        if job.get("conclusion") == "success":
            continue
        steps = job.get("steps") or []
        step_lines = [
            f"    - {s['name']}: {s.get('conclusion')}"
            for s in steps
            if s.get("conclusion") not in (None, "success", "skipped")
        ]
        job_lines.append(f'- Job "{job["name"]}": {job.get("conclusion")}\n' + "\n".join(step_lines))
    jobs_text = "\n".join(job_lines) or "No failed job/step details available."

    prompt = (
        "A CI workflow run failed. Based on the failed jobs/steps below, respond with a JSON "
        'object with exactly these keys: "summary" (one sentence), "likely_cause" (one sentence), '
        '"failed_stage" (the job/step name), "suggested_action" (one concrete next step). '
        "If the details don't indicate a clear cause, say so honestly rather than guessing.\n\n"
        f"Workflow: {workflow_name}\n\n"
        f"Failed jobs/steps:\n{jobs_text}"
    )
    content = await _chat(prompt, max_tokens=300, json_mode=True)
    return json.loads(content)


async def extract_ticket_from_thread(messages: list[dict]) -> dict:
    transcript = "\n".join(f"{m.get('user', 'unknown')}: {m.get('text', '')}" for m in messages)

    prompt = (
        "The following is a Slack thread discussing an engineering issue. Extract a Jira bug "
        "report from it as a JSON object with exactly these keys: \"title\" (short summary), "
        '"description" (1-2 sentences), "steps_to_reproduce", "expected_behavior", '
        '"actual_behavior", "severity" (one of Low/Medium/High/Critical), '
        '"priority" (one of Low/Medium/High), "suggested_assignee" (team or role, not a '
        'person\'s name unless explicitly stated). Use "Not specified in thread" for any field '
        "the thread doesn't cover — don't invent details.\n\n"
        f"Thread:\n{transcript}"
    )
    content = await _chat(prompt, max_tokens=500, json_mode=True)
    return json.loads(content)


async def phrase_status_update(facts: str) -> str:
    prompt = (
        "Based on these facts about an engineering ticket, write ONE short sentence "
        "(like a Slack status update) describing the most recent relevant activity. "
        "Don't restate all the facts — just the most notable recent one. If nothing "
        "notable stands out, say so briefly.\n\n"
        f"{facts}"
    )
    return await _chat(prompt, max_tokens=100)
