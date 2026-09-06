# DevFlow AI — Notes for Claude

- This project uses **Colima**, not Docker Desktop, as the container runtime. Run `colima start`
  before any `docker` / `docker compose` command. The `docker` CLI and `docker-compose.yml` are
  unchanged — Colima just provides the daemon/VM.
- If `docker compose` fails with "unknown command", `~/.docker/cli-plugins/docker-compose` is
  likely a dangling symlink to a removed Docker Desktop install. Fix: symlink it to the
  Homebrew-installed plugin instead, e.g.
  `ln -sf "$(brew --prefix docker-compose)/lib/docker/cli-plugins/docker-compose" ~/.docker/cli-plugins/docker-compose`.
- uvicorn's `--reload` (WatchFiles) does not reliably detect host-side edits to files under the
  `./app:/app/app` bind mount on Colima — inotify events don't propagate across the VM boundary.
  After editing files in `app/`, run `docker compose restart api` rather than assuming reload
  picked it up.
- `docker compose restart api` reuses the container's already-baked environment — it does NOT
  re-read `.env`. After editing `.env`, use `docker compose up -d --force-recreate api` instead
  (or `down && up -d`).
- Groq deprecates/renames models over time — if `GROQ_MODEL` starts 404ing with
  "does not exist or you do not have access to it", check the current catalog with
  `GET https://api.groq.com/openai/v1/models` (Bearer auth) rather than guessing a new name.
