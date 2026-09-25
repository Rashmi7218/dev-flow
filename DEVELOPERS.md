# DevFlow AI — Developer Reference

This is the maintainer-facing counterpart to [README.md](README.md). README covers "how do I run
this"; this file covers "what does this system actually do, how is it put together, where are
the seams, and what's known to be incomplete." See [RESOURCES.md](RESOURCES.md) for account/API
setup and deployment.

## Architecture

```mermaid
flowchart LR
    GHAPP[GitHub App\none webhook, every installed repo] -->|PR & workflow events| API
    JIRA[Jira Cloud] -->|status change events| API
    SLACK1[Slack] -->|commands, mentions, button clicks| API

    API[DevFlow API\nFastAPI + Postgres] --> ROUTE[Routing lookup\nrepo -> Jira project / Slack channel(s)]
    API --> GROQ[Groq LLM\nsummarize / extract / explain]
    GROQ --> API
    ROUTE --> SLACK2[Slack\nnotifications & replies]
    ROUTE --> JIRAOUT[Jira\nticket creation]
    API --> DASH[Dashboard\nevents, ticket timeline, admin]
```

Every inbound webhook is verified (HMAC for GitHub/Slack, shared-secret token for Jira),
deduplicated by provider delivery ID, processed inline in the request (no queue), and — for
GitHub/Jira events, and for Jira tickets created from Slack — routed through
`app/routing.py` to resolve which Slack channel(s)/Jira project apply before any outbound call
fires.

## Feature inventory

- **Webhook ingestion**: GitHub (`pull_request`, `workflow_run`), Jira (issue update /
  status-change), Slack (`app_mention` events, `/devflow` slash command, block-action
  interactions). All three verify the request is genuinely from that provider before touching
  anything (`app/security.py`).
- **Idempotent delivery handling** (`app/idempotency.py`): a `processed_deliveries` table keyed
  by provider delivery ID short-circuits retried webhook deliveries before any side effect
  re-fires.
- **Fault-isolated Slack fan-out** (`slack_client.post_to_channels`,
  `app/integrations/slack_client.py`): a repo can notify several bound channels; one channel
  failing (bot not invited, channel deleted, rate-limited) is logged and skipped rather than
  raising, so it neither 500s the webhook delivery nor blocks the other channels in the same
  fan-out.
- **AI layer** (`app/integrations/groq_client.py`), every call wrapped so a Groq outage/bad
  response degrades gracefully instead of breaking the underlying feature:
  - PR-opened summaries (what changed and why, from the changed-files diff)
  - CI-failure explanations (likely cause / failed stage / suggested action, from job/step data)
  - Thread → structured ticket extraction (title/description/repro/severity/etc.)
  - Natural-language ticket status phrasing for `@DevFlow <ticket-key>` mentions
- **Human-approved ticket creation**: a Slack thread proposed as a ticket shows an editable
  preview with Approve/Cancel buttons (`app/webhooks/slack.py:_propose_ticket_from_thread` /
  `handle_slack_interaction`) — nothing is created in Jira without an explicit click.
- **Dashboard** (`app/dashboard.py`, `app/static/dashboard.html`): live events feed with
  source/ticket filters, per-ticket timeline aggregating GitHub PR/workflow history with live
  Jira status, and the admin "Repo Routing" panel.
- **Admin auth** (`app/auth.py`): HTTP Basic, gating the entire dashboard router and the entire
  admin API router — nothing under `/dashboard` or `/api/` is reachable without it. Webhook
  routes are intentionally excluded (they authenticate the *provider*, not a human).
- **Multi-repo routing** (`app/routing.py`, `app/admin.py`, `RepoConfig`/`ChannelBinding`
  tables): per-repo Jira project key and one-or-more Slack channel bindings, with graceful
  fallback to the global `JIRA_PROJECT_KEY`/`SLACK_DEFAULT_CHANNEL` for anything unconfigured.
  Both lookup directions exist — repo → channels/project (for GitHub/Jira-originated events) and
  channel → repo → project (for Slack-originated ticket creation).
- **GitHub App integration** (`app/integrations/github_auth.py`): JWT-signed (RS256, App private
  key) short-lived installation tokens, one App-level webhook covering every installed repo — no
  per-repo webhook configuration, no PAT tied to one person's account. See "GitHub App setup" in
  RESOURCES.md and the "Known limitations" below.
- **Direct ticket updates** (`/devflow comment <TICKET-KEY> <text>`, `/devflow describe
  <TICKET-KEY> <text>`): comment adds immediately via `jira_client.add_comment`; describe
  appends to the existing description rather than overwriting it — fetches the ticket, renders
  its current ADF description back to plain text (`jira_client._flatten_adf`), concatenates the
  new text, and writes the combined result back. Both ack immediately and report success/failure
  via `response_url`, same pattern as `/devflow create`.

## Module reference

```
app/
  main.py                        FastAPI app, lifespan (init_db), router registration
  config.py                      Settings (pydantic-settings, reads .env)
  db.py                          Async SQLAlchemy engine/session, Base.metadata.create_all (no Alembic)
  models.py                      Event, PullRequest, WorkflowRun, Issue, ProcessedDelivery,
                                  Notification, RepoConfig, ChannelBinding
  security.py                    GitHub HMAC + Slack signature verification (provider auth)
  auth.py                        HTTP Basic dependency (human auth) for dashboard/admin routers
  idempotency.py                 Webhook delivery dedup against processed_deliveries
  correlation.py                 Ticket-key extraction from branch/title/commit text
  routing.py                     Repo <-> Jira project / Slack channel lookups + fallback
  admin.py                       /api/admin/{repos,bindings,github-app} CRUD (auth required)
  dashboard.py                   /dashboard page + /api/events, /api/tickets/* (auth required)
  static/dashboard.html          Dependency-free HTML/CSS/vanilla-JS dashboard + admin frontend
  webhooks/github.py             pull_request / workflow_run handlers, routing-aware Slack fan-out
  webhooks/jira.py               issue-update handler, routing-aware Slack fan-out
  webhooks/slack.py              events/commands/interactions — status query, thread->ticket,
                                  /devflow create, all routing-aware for Jira project resolution
  integrations/github_client.py  GitHub REST calls (PR diff, workflow run jobs)
  integrations/github_auth.py    GitHub App JWT + installation-token exchange (in-process cache)
  integrations/jira_client.py    Jira REST calls (get/create issue, comment, transitions,
                                  append-to-description via ADF flatten/re-render)
  integrations/slack_client.py   Slack Web API calls (post message, thread replies, permalink)
  integrations/groq_client.py    Groq chat-completions wrapper for all AI-layer calls
```

Test files mirror this 1:1 (`tests/test_<module>.py`), plus `tests/test_*_webhook.py` for
integration-style request→side-effect coverage per source.

## Known limitations

**Architecture / reliability**
- Events are processed inline in the webhook request, not via a queue/worker — no
  retry/backoff/DLQ semantics beyond the idempotency check. Redis/Celery (or similar) would be
  the next step at higher volume.
- `pull_requests` / `workflow_runs` are append-only history rows, not upserted by
  `(repo, number)` — revisit if "current status" queries need it.
- No token/cost tracking on Groq calls, and no schema validation on the JSON the model returns
  beyond a bare `json.loads` (a malformed response fails safely but isn't retried with a
  stricter prompt).
- Slack fan-out failures (`slack_client.post_to_channels`) are logged and skipped, not surfaced
  anywhere else — no alerting on a channel that fails repeatedly, so a misconfigured channel can
  go unnoticed until someone checks the logs or notices missing notifications. This was a
  deliberate tradeoff (fault isolation over loud failure) — see "Reliability" in README.md.

**Auth / access control**
- Jira webhook auth is a shared-secret query token, since Jira Cloud's built-in webhook UI
  doesn't support custom signing.
- Admin auth is a single shared HTTP Basic username/password for the whole dashboard/admin API —
  no per-user accounts, no audit log of who changed a routing binding.

**Multi-repo routing**
- Slack channel bindings are entered as raw channel IDs (copied from Slack) rather than picked
  from a list of channels the bot has joined — a `conversations.list`-backed picker is future
  work, not built in this pass.
- A Slack channel bound to multiple repos can't be disambiguated for ticket-creation flows
  (`/devflow create`, thread → ticket); the first bound repo's Jira project is used and a
  warning is logged (`app/routing.py:jira_project_for_channel`).

**GitHub App**
- The installation-token cache (`app/integrations/github_auth.py:_token_cache`) is a
  module-level in-process dict — correct for the single-instance deployment this project runs
  as, but would need to move to Redis/similar before running as more than one replica (the same
  ceiling the idempotency table and routing lookups don't have, since those go through Postgres).
- If the GitHub App is uninstalled or suspended, GitHub events for the affected repos stop
  arriving with no error on DevFlow's side — it just goes quiet. GitHub does send an
  `installation.deleted` webhook when this happens; DevFlow doesn't currently listen for it or
  surface a "GitHub connection lost" warning. Discussed and explicitly deferred, not an
  oversight.
- Creating/installing the App itself is a manual, human-consent GitHub flow (Settings →
  Developer settings → GitHub Apps) — there's no API to do this on a user's behalf, so it can't
  be fully automated from DevFlow's dashboard. The dashboard's "Manage GitHub App installation"
  link is the closest available shortcut (one click to GitHub's install page instead of a
  6-field manual webhook form per repo).

**Direct ticket updates**
- `/devflow describe` appends by round-tripping through a small internal markup format, not raw
  plain text: `_doc()` recognizes `*bold*` spans (this is literally what Slack sends in a slash
  command's `text` field for anything typed as bold in its rich-text composer, so this isn't
  optional) and `-`/`•`-prefixed lines, rendering them as real ADF `strong` marks and
  `bulletList`/`listItem` nodes instead of literal asterisks and flat text.  `_flatten_adf`
  renders both back out (bold → `*text*`, list items → `- text`) when reading an existing
  description, so appending twice in a row doesn't lose formatting on the second pass either.
  Richer content this format doesn't cover (headings render as plain paragraphs, tables, code
  blocks, mentions, panels, embedded media) still loses its original formatting on the way
  through, though the text content itself survives.
- Both `/devflow comment` and `/devflow describe` require an *existing* ticket — there's no
  existence check before attempting the write, so a typo'd or nonexistent ticket key just
  surfaces as a generic "❌ Failed" follow-up rather than a specific "ticket not found" message.

## Testing approach

- In-memory SQLite (`sqlite+aiosqlite:///:memory:`) via `tests/conftest.py`'s autouse
  `_reset_db` fixture — every table in `Base.metadata` is created fresh per test and dropped
  after, so a new model needs zero conftest changes to get test coverage.
- All outbound HTTP (GitHub, Jira, Slack, Groq) is mocked with `respx`, matching real endpoint
  URLs — no live credentials needed anywhere in the suite.
- Two HTTP client fixtures: `client` (unauthenticated) and `admin_client` (HTTP Basic
  pre-configured) — use `admin_client` for anything under `/dashboard` or `/api/admin`, `client`
  for webhook routes and for asserting 401s.
- `_reset_github_token_cache` (autouse) clears `github_auth`'s in-process cache between tests,
  since it's module-level state that would otherwise leak across tests.
- To add a new webhook test: follow the existing `_sign()`/payload-builder helper pattern at the
  top of `tests/test_<source>_webhook.py`, register `respx` routes for whatever outbound calls
  the code path makes, then assert on the response and on `respx` call counts/bodies.

## Extension points

- **New webhook source**: add `app/webhooks/<source>.py` following the existing three (verify →
  dedup via `is_duplicate_delivery` → parse → route via `app/routing.py` if it should respect
  multi-repo config → side effects), register its router in `app/main.py`.
- **New admin-configurable setting**: add a model to `app/models.py` (auto-picked-up by
  `create_all`, no migration needed), CRUD endpoints in `app/admin.py` (already auth-gated at
  the router level), and a form/table in the "Repo Routing" card of
  `app/static/dashboard.html` following the existing `repo-config-*`/`binding-*` pattern.
- **New AI-powered flow**: add a function to `app/integrations/groq_client.py` following the
  existing wrap-in-try/except-and-degrade convention used by every call site in
  `app/webhooks/github.py`/`slack.py` — the AI layer should never be able to break the
  deterministic path around it.
