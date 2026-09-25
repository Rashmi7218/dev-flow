# DevFlow AI — Manual QA Checklist

This is a manual test plan for verifying the newly added features against a **real, deployed**
instance (local `docker compose` or Render) — real GitHub repo, real Jira project, real Slack
workspace. It complements, not replaces, the automated suite: `pytest -v` already covers all of
this at the unit/integration level with mocked HTTP (see DEVELOPERS.md → "Testing approach"),
but only a live run catches real account/permission/config mistakes (missing scopes, bot not
invited, App not installed) — which is exactly what's bitten us so far in this project.

Each case: **Setup** (what has to be true first) → **Steps** → **Expected**.

## 1. Admin auth (`app/auth.py`)

| # | Setup | Steps | Expected |
|---|-------|-------|----------|
| AUTH-1 | — | `curl -i https://<host>/dashboard` with no credentials | `401`, `WWW-Authenticate: Basic` header |
| AUTH-2 | — | Load `/dashboard` in a browser with the wrong password | `401` |
| AUTH-3 | — | Load `/dashboard` with correct `ADMIN_USERNAME`/`ADMIN_PASSWORD` | `200`, dashboard renders |
| AUTH-4 | — | `curl -i https://<host>/api/admin/repos` with no credentials | `401` (proves the API is gated too, not just the HTML page) |

## 2. Multi-repo routing (`app/routing.py`, `app/admin.py`)

| # | Setup | Steps | Expected |
|---|-------|-------|----------|
| ROUTE-1 | — | Dashboard → "Jira project per repo" → add `owner/repo` → `KAN` → Save | Row appears in the table immediately |
| ROUTE-2 | — | Dashboard → "Slack channel bindings" → add `owner/repo` → a real channel ID → Add | Row appears in the table immediately |
| ROUTE-3 | ROUTE-2 done | Click "Remove" on that binding | Row disappears |
| ROUTE-4 | Repo bound to project `X`, bot **not yet invited** to bound channel | Open a PR on that repo | GitHub delivery shows `200`; **no** Slack message; server log has `Failed to post to Slack channel <id>` (this is the fault-isolation behavior, not a bug — see SLACK-2 below) |
| ROUTE-5 | Repo bound to a channel, bot invited | Open a PR on that repo | Notification lands in **that specific channel**, not `SLACK_DEFAULT_CHANNEL` |
| ROUTE-6 | Repo has **no** binding | Open a PR on that repo | Notification lands in `SLACK_DEFAULT_CHANNEL` (fallback, not silence) |
| ROUTE-7 | Repo bound to Jira project `X`, channel bound to that repo, bot in a thread in that channel | In that channel: `/devflow create fix the thing` | Ticket is created in project `X`, not the global default `JIRA_PROJECT_KEY` |
| ROUTE-8 | Channel has **no** repo bound | `/devflow create fix the thing` in that channel | Ticket created in the global default `JIRA_PROJECT_KEY` |

## 3. GitHub App integration (`app/integrations/github_auth.py`)

| # | Setup | Steps | Expected |
|---|-------|-------|----------|
| GH-1 | — | GitHub → repo → Settings → **no** "Webhooks" entry pointing at DevFlow's old URL | Confirms the old per-repo webhook (if this repo predates the App) was removed — leaving it causes duplicate notifications |
| GH-2 | — | `github.com/settings/apps/<slug>` → Install App → confirm target repo is listed (explicitly or via "All repositories") | Repo shows as installed |
| GH-3 | GH-2 done | Open a PR on the repo | `github.com/settings/apps/<slug>` → **Advanced** → Recent Deliveries shows a `pull_request` / `opened` entry with response `200` |
| GH-4 | GH-3 | Check the Slack message for that PR | Includes an AI-generated summary line (proves `get_changed_files` succeeded via the App's installation token, not just the base "PR Opened" text) |
| GH-5 | — | Push a commit that fails CI on a tracked repo | Slack message includes "Likely cause" / "Failed stage" / "Suggested action" (proves `get_workflow_run_jobs` succeeded via App auth) |
| GH-6 | App not yet installed on a repo | Open a PR on that (uninstalled) repo | **No** delivery appears in the App's Recent Deliveries at all — confirms "not installed" is silent on GitHub's side, matching README's Troubleshooting |

## 4. Slack fault isolation (`app/integrations/slack_client.py`)

| # | Setup | Steps | Expected |
|---|-------|-------|----------|
| SLACK-1 | Repo bound to two channels, bot in **both** | Open a PR | Message posted to **both** channels |
| SLACK-2 | Repo bound to two channels, bot in **only one** | Open a PR | GitHub delivery still shows `200` (not `500`); message **does** land in the one channel the bot is in; server log shows `not_in_channel` for the other |
| SLACK-3 | — | Remove the bot from a bound channel, then trigger a new event for that repo (not a redelivery — see README's Troubleshooting on why redelivery won't re-trigger the Slack call) | `200` delivery, no Slack message, `not_in_channel` in the logs |

## 5. Autonomous agent (`app/agent/`)

| # | Setup | Steps | Expected |
|---|-------|-------|----------|
| AGENT-1 | Bot in a channel | `/devflow agent do something vague with no ticket` | Immediate ephemeral usage error — no run created |
| AGENT-2 | A real ticket key exists, PRs/CI data exist for it | `/devflow agent take <TICKET> through the post-merge workflow` | Immediate ephemeral "Starting agent run for `<TICKET>`..." |
| AGENT-3 | AGENT-2 just ran | Dashboard → "Agent Runs" card → Refresh | New row appears with that ticket/goal; status progresses (`executing` → `done`/`waiting_approval`) as you refresh |
| AGENT-4 | Ticket's linked CI run is currently **failing** | Let AGENT-2's run finish | Final Slack summary reflects the failure (not a status-transition attempt) — proves re-planning: the model changed behavior based on what it observed, not a fixed branch |
| AGENT-5 | Ticket's linked CI/PR state supports moving status to e.g. "Done" | Let the run reach that point | An Approve/Reject prompt appears in Slack; dashboard shows that run's status as `waiting_approval` |
| AGENT-6 | AGENT-5's prompt is live | Click **Approve** | Jira ticket's status actually changes (verify in Jira); run resumes and reaches `done`; final summary posted to Slack |
| AGENT-7 | Repeat AGENT-5 with a fresh run | Click **Reject** | Jira ticket status **unchanged**; run still resumes and reaches `done` with a summary reflecting the rejection (proves re-planning around a "no", not a dead end) |
| AGENT-8 | Any completed run | Dashboard → "Agent Runs" → click the run | Full step-by-step trace renders — each tool call, its result, and the approval prompt/outcome if any |
| AGENT-9 | — | Trigger a goal that's ambiguous enough the model can't finish in a few turns (or temporarily point `GROQ_MODEL` at something that mostly ignores tool instructions) | Run ends with status `failed` and a Slack message saying it needs a human — not an infinite loop (bounded by `MAX_ITERATIONS`) |

## 6. Comment / describe commands (`app/integrations/jira_client.py`, `app/webhooks/slack.py`)

| # | Setup | Steps | Expected |
|---|-------|-------|----------|
| TICKET-1 | A real ticket key exists | `/devflow comment <TICKET-KEY> this is blocked on infra` | Immediate ephemeral "Adding comment to `<TICKET-KEY>`..."; Jira ticket has the new comment shortly after |
| TICKET-2 | — | `/devflow comment <TICKET-KEY>` (no comment text) | Immediate ephemeral usage error, no Jira call made |
| TICKET-3 | A real ticket key exists **with no description yet** | `/devflow describe <TICKET-KEY> Acceptance criteria: DB reachable in dev and prod` | Ticket's description field is set to that text |
| TICKET-4 | Same ticket as TICKET-3, which now has a description | `/devflow describe <TICKET-KEY> Also needs a rollback plan` | Ticket's description now has **both** the original text and the new text (appended, not replaced) — verify in Jira directly |
| TICKET-5 | A ticket whose description has a bold label and a bullet list (either written in Jira directly, or by a prior `/devflow describe` with `*bold*`/`- item` syntax) | `/devflow describe <TICKET-KEY> one more line` | New line is appended; the existing bold text and bullet list are still rendered as real bold/bullets, not flattened to plain text — this is what TICKET-3/4 exercise for content DevFlow itself wrote. Content Jira's own rich-text editor added that isn't bold/bullets/plain text (tables, code blocks, mentions, panels) is the case that still degrades — see DEVELOPERS.md's Known limitations |
| TICKET-6 | A ticket key that doesn't exist | `/devflow comment NOPE-999 test` | Ephemeral "Adding comment to NOPE-999..." ack, followed by a "❌ Failed" follow-up message (the ticket lookup/write fails, doesn't hang or 500) |

## Notes

- Steps involving GitHub/Jira/Slack require real, working credentials for all three — this isn't
  something CI can run; it's for validating an actual deployment before/after a change.
- If ROUTE-4/SLACK-2/SLACK-3 don't behave as described (i.e. you get a `500` instead of a clean
  `200` with a logged failure), that's a regression in the fault-isolation fix — see README's
  "Reliability" section.
- AGENT-9 is the hardest to force deterministically against a live model; the automated
  equivalent (`tests/test_agent_loop.py::test_exhausting_max_iterations_marks_run_failed_not_infinite`)
  is the reliable version of this case.
