# DevFlow AI

![CI](https://github.com/Rashmi7218/dev-flow/actions/workflows/ci.yml/badge.svg)

Event-driven engineering workflow automation connecting GitHub, Jira, and Slack. See
[devflow-ai-project-idea.md](devflow-ai-project-idea.md) for the full product vision and
[RESOURCES.md](RESOURCES.md) for the account/API setup checklist.

Phase 1 (GitHub/Jira/Slack event wiring) is done: PR/workflow events from GitHub and status
events from Jira are posted to Slack, and a Slack slash command creates Jira tickets. Phase 2
(AI layer, via Groq) is done too: PR summaries, CI failure explanations, Slack thread → Jira
ticket (with human approval), and natural-language ticket status queries via `@DevFlow`. Phase 3
has started with a read-only web dashboard at `/dashboard`.

## Setup

1. Work through [RESOURCES.md](RESOURCES.md) to create the GitHub/Jira/Slack accounts and
   credentials.
2. Copy `.env.example` to `.env` and fill in the values.
3. Start an ngrok tunnel: `ngrok http 8000` (or `ngrok http --url=<your-static-domain> 8000`)
   and point the GitHub, Jira, and Slack webhook configs at
   `https://<your-domain>/webhooks/github`, `/webhooks/jira?token=...`, and
   `/webhooks/slack/events` / `/webhooks/slack/commands` respectively.
4. Start Colima (Docker Desktop replacement): `colima start`. Then `docker compose up --build`
5. Check `http://localhost:8000/health`.
6. Browse recent events and per-ticket timelines at `http://localhost:8000/dashboard`.

For an always-on public deployment instead of local + ngrok, see "Deployment (Render)" in
[RESOURCES.md](RESOURCES.md) — `render.yaml` provisions the whole stack from one file.

## Layout

```
app/
  main.py            FastAPI app + router registration
  config.py          Settings loaded from .env
  db.py              Async SQLAlchemy engine/session
  models.py          events, pull_requests, workflow_runs, issues, notifications
  security.py        GitHub HMAC + Slack signature verification
  correlation.py      Ticket-key extraction (branch/title/commit -> AITENDER-2445)
  integrations/       Thin HTTP clients for GitHub, Jira, Slack, Groq APIs
  webhooks/           Webhook route handlers per source
  dashboard.py        Read-only /dashboard page + /api/events, /api/tickets/{key}/timeline
  static/dashboard.html  Dependency-free HTML/JS dashboard frontend
```

## Testing

```
pip install -r requirements-dev.txt
pytest -v
```

Tests run against an in-memory SQLite DB and mock all outbound HTTP (GitHub, Jira, Slack, Groq)
via `respx` — no live credentials or Docker needed. CI (`.github/workflows/ci.yml`) runs this
suite on every push/PR.

## Reliability

Webhook deliveries are deduped by their provider-supplied delivery ID (`X-GitHub-Delivery` for
GitHub, `X-Atlassian-Webhook-Identifier` for Jira, `event_id` for Slack Events API) against a
`processed_deliveries` table (`app/idempotency.py`). A provider retry of an already-processed
delivery returns `{"status": "duplicate"}` immediately, without re-posting to Slack, re-creating
Jira tickets, or re-calling Groq — this matters because GitHub and Slack both retry webhook
deliveries on timeout/non-2xx responses in production.

## Known Phase 1 simplifications

- Events are processed inline in the webhook request, not via a queue/worker (Redis/Celery
  is a Phase 4 reliability addition).
- `pull_requests` / `workflow_runs` are append-only history rows rather than upserted by
  `(repo, number)` — simplest for now, revisit if "current status" queries need it.
- Jira webhook auth is a shared-secret query token, since Jira Cloud's built-in webhook UI
  doesn't support custom signing.

## License

[MIT](LICENSE)
