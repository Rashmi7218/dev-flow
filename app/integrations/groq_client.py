import httpx

from app.config import settings

BASE_URL = "https://api.groq.com/openai/v1"


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

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": settings.groq_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 200,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
