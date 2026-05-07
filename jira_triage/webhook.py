from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Sequence

from fastapi import Body, FastAPI, HTTPException, Query

from .config import ConfigError
from .core import TriageError, normalize_ticket_key, triage
from .jira_client import JiraError

app = FastAPI(title="jira-triage", version="0.1.0")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "f", "no", "n", "off", ""}:
            return False
    return False


def _extract_ticket_key(payload: Any) -> str:
    if isinstance(payload, dict):
        issue = payload.get("issue")
        if isinstance(issue, dict):
            key = issue.get("key")
            if isinstance(key, str) and key.strip():
                return normalize_ticket_key(key)
        if isinstance(issue, str) and issue.strip():
            return normalize_ticket_key(issue)

        for k in ("issueKey", "ticket_id", "ticket", "key"):
            v = payload.get(k)
            if isinstance(v, str) and v.strip():
                return normalize_ticket_key(v)

    try:
        return normalize_ticket_key(json.dumps(payload, ensure_ascii=False))
    except Exception:
        return normalize_ticket_key(str(payload))


@app.post("/jira")
async def jira_webhook(payload: Any = Body(...), open: bool = Query(default=False), process_logs: bool = Query(default=False)) -> dict[str, str]:
    try:
        ticket_key = _extract_ticket_key(payload)
        open_requested = open or (isinstance(payload, dict) and _truthy(payload.get("open")))
        process_logs_requested = process_logs or (isinstance(payload, dict) and _truthy(payload.get("process_logs")))
        result = triage(ticket_key, mode="webhook", open_cursor=open_requested, process_logs=process_logs_requested)
    except TriageError as e:
        status = 400
        cause = e.__cause__
        if isinstance(cause, ConfigError):
            status = 500
        elif isinstance(cause, JiraError):
            status = 502
        raise HTTPException(status_code=status, detail=str(e)) from e

    return {
        "ticket_id": result.ticket_key,
        "repo_root": str(result.repo_root),
        "output_dir": str(result.output_dir),
        "cursor_context_path": str(result.context_path),
        "bundle_context_path": str(result.bundle_context_path),
    }


def _default_port() -> int:
    raw = os.getenv("WEBHOOK_PORT", "8080")
    try:
        port = int(raw)
    except ValueError as e:
        raise SystemExit(f"Invalid WEBHOOK_PORT: {raw!r}") from e
    if not (1 <= port <= 65535):
        raise SystemExit(f"Invalid WEBHOOK_PORT (out of range): {port}")
    return port


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jira-cursor-webhook",
        description="Run a FastAPI webhook that turns Jira payloads into Cursor-ready context bundles.",
    )
    p.add_argument("--host", default=os.getenv("WEBHOOK_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=_default_port())
    p.add_argument("--reload", action="store_true", help="Enable auto-reload (development only).")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    try:
        import uvicorn  # type: ignore
    except Exception as e:
        print("Missing dependency: uvicorn. Reinstall with project dependencies.", file=sys.stderr)
        print(str(e), file=sys.stderr)
        return 1

    uvicorn.run("jira_triage.webhook:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

