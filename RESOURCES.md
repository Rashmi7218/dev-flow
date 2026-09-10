# DevFlow AI — Resource & Setup Plan

Accounts, local dev setup (Docker Compose via Colima + ngrok), and deployment (Render) needed to
run the full system — GitHub + Jira + Slack event wiring, plus the Groq-powered AI layer.

## Accounts / Resources

| # | Resource | Used for | Cost | Setup notes |
|---|----------|----------|------|-------------|
| 1 | GitHub account | Source events, Actions | Free | Create a throwaway repo (e.g. `devflow-test-repo`) to generate real PR/workflow events |
| 2 | GitHub App | Ingest PR/workflow events (one webhook for every installed repo); call GitHub API | Free | See "GitHub App setup" below — one-time, org-level |
| 3 | Atlassian account + Jira Cloud site | Ticket tracking, status webhooks | Free (Free plan, up to 10 users) | Create a site at `<you>.atlassian.net`, one project (e.g. key `KAN`) |
| 4 | Jira API token | `jira.get_issue()`, `create_issue()`, `add_comment()` | Free | id.atlassian.com → Security → API tokens. Basic auth (email + token) — no OAuth app needed for solo use |
| 5 | Jira webhook | Status-changed / issue-updated events | Free | Jira Admin → System → WebHooks — you're the site admin, so no Connect/OAuth app required. Point at ngrok URL + `/webhooks/jira?token=...` |
| 6 | Slack workspace + Slack App | Notifications, slash command, `@DevFlow` mentions | Free | Create a personal workspace at slack.com, register app at api.slack.com/apps. Scopes: `chat:write`, `app_mentions:read`, `commands`, `channels:history`. **Invite the bot to `SLACK_DEFAULT_CHANNEL` and to every channel you bind a repo to on the dashboard** — `chat.postMessage` fails with `not_in_channel` otherwise (`/invite @DevFlow` in each channel, or Channel details → Integrations → Add app) |
| 7 | ngrok account | Public HTTPS endpoint for all three webhooks during local dev | Free | Free plan includes 1 reserved static domain, so webhook URLs don't change on restart |
| 8 | Colima + Docker Compose | Local Postgres + FastAPI app | Free | Colima runs the container VM/daemon (Docker Desktop replacement on macOS); `docker`/`docker compose` CLI commands work unchanged. `colima start` before `docker compose up` |
| 9 | Groq API key | PR/failure summarization, ticket generation (Phase 2) | Free tier, usage-based beyond that | console.groq.com/keys. `openai/gpt-oss-120b` for summarization/reasoning quality; check `GET /openai/v1/models` for the current catalog since Groq deprecates/renames models over time |

## GitHub App setup

One-time, at the org level — every repo the org wants DevFlow to track is added to this same
installation later (via the dashboard's "Manage GitHub App installation" link, or directly on
GitHub), no per-repo webhook configuration needed.

1. GitHub → your org or account → **Settings → Developer settings → GitHub Apps → New GitHub
   App**.
2. **Homepage URL**: anything (e.g. this repo's URL). **Webhook URL**: your ngrok URL (or Render
   URL once deployed) + `/webhooks/github`. **Webhook secret**: generate one — this becomes
   `GITHUB_WEBHOOK_SECRET`, the same secret for every repo the app is installed on.
3. **Repository permissions**: `Pull requests: Read-only`, `Actions: Read-only` (`Metadata:
   Read-only` is included automatically — that's all DevFlow calls today: PR diffs, workflow run
   jobs).
4. **Subscribe to events**: `Pull request`, `Workflow run`.
5. **Where can this GitHub App be installed?**: "Only on this account" is fine for a single org.
6. Create the app. Note the **App ID** (`GITHUB_APP_ID`) and **App slug** — the URL-safe name
   shown in the app's settings URL (`GITHUB_APP_SLUG`, used to build the dashboard's install
   link).
7. **Generate a private key** (scroll down on the app's settings page) — downloads a `.pem` file.
   Its contents become `GITHUB_APP_PRIVATE_KEY` (see `.env.example` for the exact multi-line
   quoted format `.env` expects).
8. **Install the app**: from the app's settings page, "Install App" → pick the org/account →
   choose "All repositories" or select specific ones (e.g. `devflow-test-repo`). You can add or
   remove repos here at any time later without touching any secret.

## Deferred to later phases (do not set up yet)

- **Microsoft Teams / Azure Bot Service** — Phase 3. Requires Azure AD app registration + Bot Framework registration + Teams dev tenant.
- **OAuth 2.0 / RBAC / multi-tenancy** — Phase 4. Basic auth + PAT/API tokens are enough for a solo instance.
- **Redis / Celery** — Introduced in Phase 4 reliability work (retries, DLQ, idempotency). Phase 1 processes events inline in the request/response cycle, which is simpler and sufficient at this scale.
- **Vector DB for RAG** — Phase 3. When needed, Postgres + `pgvector` avoids a new account entirely.
- ~~**Cloud hosting (Render/Fly/Railway/etc.)**~~ — Done. See "Deployment (Render)" below.

## Setup order

1. GitHub App (see "GitHub App setup" above) — create once, install on your test repo(s)
2. Jira Cloud site → project → API token → webhook
3. Slack app → bot token → signing secret → slash command `/devflow`
4. ngrok static domain → wire the GitHub App webhook, Jira webhook, and Slack URLs to it
5. `colima start`, then `docker compose up` (Postgres + FastAPI)
6. Groq API key (Phase 2)

## Cost reality check

Everything above is $0, including Groq at light dev usage (a handful of summaries/day comfortably
fits Groq's free tier) — not a real budget line for a portfolio project.

## Deployment (Render)

`render.yaml` in the repo root is a Render "Blueprint" — infrastructure as code that provisions
both the web service and a Postgres database from one file.

1. Push the repo to GitHub (already done).
2. On [render.com](https://render.com), sign up (no credit card needed for free tier) → **New** →
   **Blueprint** → connect the `dev-flow` GitHub repo. Render detects `render.yaml` automatically.
3. Render provisions `devflow-db` (free Postgres) and `devflow-api` (free web service built from
   the `Dockerfile`), wiring `DATABASE_URL` between them automatically.
4. Render will prompt for the remaining secret env vars (marked `sync: false` in `render.yaml`) —
   paste in the same values from your local `.env`: `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_ID`,
   `GITHUB_APP_PRIVATE_KEY` (Render's env var editor accepts multi-line values directly — paste
   the PEM as-is, no escaping needed), `GITHUB_APP_SLUG`, `JIRA_BASE_URL`, `JIRA_EMAIL`,
   `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`, `JIRA_WEBHOOK_TOKEN`, `SLACK_BOT_TOKEN`,
   `SLACK_SIGNING_SECRET`, `SLACK_DEFAULT_CHANNEL`, `GROQ_API_KEY`, `ADMIN_USERNAME`,
   `ADMIN_PASSWORD`.
5. Deploy. Once live, re-point the GitHub App's webhook URL, the Jira webhook config, and the
   Slack URLs (event subscriptions, slash command, Interactivity request URL) from the ngrok URL
   to `https://<your-service>.onrender.com/...` — this URL is permanent, so ngrok is no longer
   needed at all.
6. Check `https://<your-service>.onrender.com/health` and `/dashboard`.

**Free-tier caveats**: the web service spins down after 15 minutes idle (first request after that
takes ~30-50s to cold-start); the free Postgres database expires after 90 days and needs
recreating. Fine for a portfolio demo; upgrade to a paid plan for an always-on instance.
