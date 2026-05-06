from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping


def _parse_bool(raw: str | None) -> bool:
    if raw is None:
        return False
    v = raw.strip().lower()
    return v in {"1", "true", "t", "yes", "y", "on"}


def _enabled() -> bool:
    # Default to off; enable by setting JIRA_TRIAGE_DEBUG=1 (or by configuring a log path/session id).
    if _parse_bool(os.environ.get("JIRA_TRIAGE_DEBUG")):
        return True
    if (os.environ.get("JIRA_TRIAGE_DEBUG_LOG_PATH") or "").strip():
        return True
    if (os.environ.get("JIRA_TRIAGE_DEBUG_SESSION_ID") or "").strip():
        return True
    return False


def _log_path() -> Path:
    raw = (os.environ.get("JIRA_TRIAGE_DEBUG_LOG_PATH") or "").strip()
    p = Path(raw).expanduser() if raw else Path(".cursor/jira-triage.debug.ndjson")
    try:
        return p.resolve()
    except Exception:
        return p


def _session_id() -> str:
    return (os.environ.get("JIRA_TRIAGE_DEBUG_SESSION_ID") or "").strip()


def debug_log(
    *,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: Mapping[str, Any] | None = None,
) -> None:
    try:
        if not _enabled():
            return

        log_path = _log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        payload: dict[str, Any] = {
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": dict(data or {}),
            "timestamp": int(time.time() * 1000),
        }

        sid = _session_id()
        if sid:
            payload["sessionId"] = sid

        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # Debug logging must never break normal execution.
        return

