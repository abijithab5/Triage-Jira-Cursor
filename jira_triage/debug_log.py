from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping


_LOG_PATH = Path("/Users/abijithp/Desktop/Jira-triage/.cursor/debug-e08569.log")
_SESSION_ID = "e08569"


def debug_log(
    *,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: Mapping[str, Any] | None = None,
) -> None:
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

