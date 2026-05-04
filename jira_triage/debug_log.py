from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Mapping


def _truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    v = str(raw).strip().lower()
    return v in {"1", "true", "t", "yes", "y", "on"}


_ENABLED = _truthy(os.getenv("JIRA_TRIAGE_DEBUG_LOG"))
_DEFAULT_PATH = Path.home() / ".cursor" / "jira-triage.debug.jsonl"
_LOG_PATH = Path(os.getenv("JIRA_TRIAGE_DEBUG_LOG_PATH", str(_DEFAULT_PATH))).expanduser()
_SESSION_ID = os.getenv("JIRA_TRIAGE_DEBUG_SESSION_ID") or secrets.token_hex(3)


def debug_log(
    *,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: Mapping[str, Any] | None = None,
) -> None:
    if not _ENABLED:
        return
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessionId": _SESSION_ID,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": dict(data or {}),
            "timestamp": int(time.time() * 1000),
        }
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # Debug logging must never break normal execution.
        return

