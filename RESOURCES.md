# DevFlow AI — Phase 1 Resource & Setup Plan

Scope: Phase 1 MVP only (GitHub + Jira + Slack, no AI yet). Solo/local development using
Docker Compose (via Colima) + ngrok — no cloud hosting required. LLM provider chosen for when Phase 2
starts: Groq.

## Accounts / Resources

| # | Resource | Used for | Cost | Setup notes |
|---|----------|----------|------|-------------|
| 1 | GitHub account | Source events, Actions | Free | Create a throwaway repo (e.g. `devflow-test-repo`) to generate real PR/workflow events |
| 2 | GitHub webhook + PAT | Ingest PR/workflow events; call GitHub API | Free | Repo → Settings → Webhooks → point at your ngrok URL + `/webhooks/github`. Fine-grained PAT covers read access to PRs/workflow runs. Full GitHub App/OAuth is deferred to Phase 4 |
| 3 | Atlassian account + Jira Cloud site | Ticket tracking, status webhooks | Free (Free plan, up to 10 users) | Create a site at `<you>.atlassian.net`, one project (e.g. key `AITENDER`) |
| 4 | Jira API token | `jira.get_issue()`, `create_issue()`, `add_comment()` | Free | id.atlassian.com → Security → API tokens. Basic auth (email + token) — no OAuth app needed for solo use |
| 5 | Jira webhook | Status-changed / issue-updated events | Free | Jira Admin → System → WebHooks — you're the site admin, so no Connect/OAuth app required. Point at ngrok URL + `/webhooks/jira?token=...` |
| 6 | Slack workspace + Slack App | Notifications, slash command, `@DevFlow` mentions | Free | Create a personal workspace at slack.com, register app at api.slack.com/apps. Scopes: `chat:write`, `app_mentions:read`, `commands`, `channels:history` |
| 7 | ngrok account | Public HTTPS endpoint for all three webhooks during local dev | Free | Free plan includes 1 reserved static domain, so webhook URLs don't change on restart |
| 8 | Colima + Docker Compose | Local Postgres + FastAPI app | Free | Colima runs the container VM/daemon (Docker Desktop replacement on macOS); `docker`/`docker compose` CLI commands work unchanged. `colima start` before `docker compose up` |
| 9 | Groq API key | PR/failure summarization, ticket generation (Phase 2) | Free tier, usage-based beyond that | console.groq.com/keys. `openai/gpt-oss-120b` for summarization/reasoning quality; check `GET /openai/v1/models` for the current catalog since Groq deprecates/renames models over time |

## Deferred to later phases (do not set up yet)

- **Microsoft Teams / Azure Bot Service** — Phase 3. Requires Azure AD app registration + Bot Framework registration + Teams dev tenant.
- **OAuth 2.0 / RBAC / multi-tenancy** — Phase 4. Basic auth + PAT/API tokens are enough for a solo instance.
- **Redis / Celery** — Introduced in Phase 4 reliability work (retries, DLQ, idempotency). Phase 1 processes events inline in the request/response cycle, which is simpler and sufficient at this scale.
- **Vector DB for RAG** — Phase 3. When needed, Postgres + `pgvector` avoids a new account entirely.
- **Cloud hosting (Render/Fly/Railway/etc.)** — Only needed once you want an always-on demo instead of local + ngrok.

## Setup order

1. GitHub test repo → webhook → PAT
2. Jira Cloud site → project → API token → webhook
3. Slack app → bot token → signing secret → slash command `/devflow`
4. ngrok static domain → wire all three webhook URLs to it
5. `colima start`, then `docker compose up` (Postgres + FastAPI)
6. Groq API key (Phase 2)

## Cost reality check

Everything above is $0, including Groq at light dev usage (a handful of summaries/day comfortably
fits Groq's free tier) — not a real budget line for a portfolio project.
