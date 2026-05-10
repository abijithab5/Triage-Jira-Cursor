from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Mapping


def _get_debug_log_path() -> Path:
    """Get debug log path from environment or use project-relative default."""
    # Try environment variable first
    env_path = os.getenv("DEBUG_LOG_PATH")
    if env_path:
        return Path(env_path)
    
    # Default to logs/ directory in project root
    project_root = Path(__file__).parent.parent
    logs_dir = project_root / "logs"
    logs_dir.mkdir(exist_ok=True)
    
    # Use a unique session ID for the filename
    session_id = os.getenv("DEBUG_SESSION_ID", str(uuid.uuid4())[:8])
    return logs_dir / f"debug-{session_id}.log"


_LOG_PATH = _get_debug_log_path()
_SESSION_ID = os.getenv("DEBUG_SESSION_ID", str(uuid.uuid4())[:8])


def debug_log(
    message_or_run_id: str | None = None,
    data_or_hypothesis_id: Any | None = None,
    *,
    run_id: str | None = None,
    hypothesis_id: str | None = None,
    location: str | None = None,
    message: str | None = None,
    data: Mapping[str, Any] | None = None,
) -> None:
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        # Determine which API is being used
        if run_id is not None:
            # Modern keyword-only API
            final_run_id = run_id
            final_hypothesis_id = hypothesis_id or "unknown"
            final_location = location or "unknown"
            final_message = message or "unknown"
            final_data = dict(data or {})
        else:
            # Legacy positional API: debug_log(message, data)
            final_run_id = "legacy"
            final_hypothesis_id = "legacy"
            final_location = "legacy"
            final_message = message_or_run_id or "unknown"
            final_data = dict(data_or_hypothesis_id or {}) if isinstance(data_or_hypothesis_id, dict) else {"raw_data": data_or_hypothesis_id}

        payload = {
            "sessionId": _SESSION_ID,
            "runId": final_run_id,
            "hypothesisId": final_hypothesis_id,
            "location": final_location,
            "message": final_message,
            "data": final_data,
            "timestamp": int(time.time() * 1000),
        }
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # Debug logging must never break normal execution.
        return

