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

