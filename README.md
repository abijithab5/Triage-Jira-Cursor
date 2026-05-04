# Jira-triage (Jira → Cursor)

This starter fetches a Jira issue (MCP-first, REST fallback), optionally fetches related logs (API + local folder fallback), and writes:

- A bundle to `out/<TICKET_KEY>/`
- A Cursor context file to `.cursor/context/TICKET.md` in your repo

## Setup

```bash
cd /Users/abijithp/Desktop/Jira-triage
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Edit `.env` and set your Jira credentials (do not commit your real token). The tools will load `.env` automatically when present.

Auth options:
- Bearer PAT: set `JIRA_AUTH_MODE=bearer` and `JIRA_TOKEN=...` (or set `JIRA_PAT=...`)
- Basic: set `JIRA_AUTH_MODE=basic`, `JIRA_USER=...`, `JIRA_TOKEN=...`

Jira source:
- `JIRA_SOURCE=auto` (default): MCP primary, REST fallback
- `JIRA_SOURCE=mcp`: MCP only
- `JIRA_SOURCE=api`: REST only

## Manual CLI

```bash
jira-cursor PROJ-123
jira-cursor https://jira.telekom.de/browse/PROJ-123
jira-cursor PROJ-123 --no-open
jira-cursor PROJ-123 --logs-dir "/path/to/logs"
jira-cursor PROJ-123 --attach
```

By default the CLI opens Cursor on your repo root (git top-level or `--repo`), with `.cursor/context/TICKET.md` updated.

## Webhook server (FastAPI)

```bash
jira-cursor-webhook --host 0.0.0.0 --port 8080
```

Send a Jira webhook-like payload:

```bash
curl -X POST "http://localhost:8080/jira" \
  -H 'Content-Type: application/json' \
  -d '{"issue":{"key":"PROJ-123"}}'
```

To request opening Cursor from the webhook, pass `?open=true` **and** set `WEBHOOK_ALLOW_OPEN=true`:

```bash
curl -X POST "http://localhost:8080/jira?open=true" \
  -H 'Content-Type: application/json' \
  -d '{"issue":{"key":"PROJ-123"}}'
```

The webhook returns JSON like `{ ticket_id, output_dir, cursor_context_path, bundle_context_path }`.

## Outputs

- `out/<KEY>/issue.json`
- `out/<KEY>/context.md` (bundle context)
- `out/<KEY>/analysis.md` (fill this in via Cursor)
- `out/<KEY>/repo_paths.json`
- `out/<KEY>/logs.*` (only if `LOG_API_URL` is set)
- `.cursor/context/TICKET.md` (Cursor context, in your repo)

