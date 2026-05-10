from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any, Sequence

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .config import ConfigError, load_config
from .core import TriageError, normalize_ticket_key, triage
from .debug_log import debug_log
from .duplicate_detection import should_skip_processing
from .jira_client import JiraError, fetch_issue
from .logging_config import setup_webhook_logging

# Initialize webhook logger
webhook_logger = setup_webhook_logging()

app = FastAPI(title="jira-triage", version="0.1.0")


class WebhookLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all incoming webhook requests."""
    
    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate correlation ID
        correlation_id = str(uuid.uuid4())[:8]
        
        debug_log(
            run_id="debug",
            hypothesis_id="A",
            location="jira_triage/webhook.py:WebhookLoggingMiddleware.dispatch",
            message="HTTP request received in middleware",
            data={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": str(request.url.path),
                "client_ip": request.client.host if request.client else "unknown",
                "user_agent": request.headers.get("user-agent", "unknown"),
                "content_length": request.headers.get("content-length", "unknown"),
            },
        )

        # Log request start
        start_time = time.time()
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")
        content_length = request.headers.get("content-length", "unknown")
        
        webhook_logger.info(
            "Request started: correlation_id=%s method=%s path=%s client_ip=%s user_agent=%s content_length=%s",
            correlation_id, request.method, request.url.path, client_ip, user_agent, content_length
        )
        
        # Add correlation ID to request state
        request.state.correlation_id = correlation_id
        
        # Process request
        try:
            response = await call_next(request)
            
            # Log successful response
            duration = time.time() - start_time
            webhook_logger.info(
                "Request completed: correlation_id=%s status_code=%d duration=%.3fs",
                correlation_id, response.status_code, duration
            )
            
            # Add correlation ID to response headers
            response.headers["X-Correlation-ID"] = correlation_id
            return response
            
        except Exception as e:
            # Log error
            duration = time.time() - start_time
            webhook_logger.error(
                "Request failed: correlation_id=%s error=%s duration=%.3fs",
                correlation_id, str(e), duration, exc_info=True
            )
            raise


# Add the logging middleware
app.add_middleware(WebhookLoggingMiddleware)


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
async def jira_webhook(
    request: Request,
    payload: Any = Body(...),
    open: bool = Query(default=False),
    repo: str | None = Query(default=None),
    logs_dir: str | None = Query(default=None),
) -> dict[str, Any]:
    # Get correlation ID from middleware
    correlation_id = getattr(request.state, 'correlation_id', 'unknown')
    
    # Log payload processing start
    try:
        debug_log(
            run_id="debug",
            hypothesis_id="C,D",
            location="jira_triage/webhook.py:jira_webhook",
            message="Processing webhook payload",
            data={
                "correlation_id": correlation_id,
                "payload_type": type(payload).__name__,
                "payload_is_dict": isinstance(payload, dict),
                "payload_keys": list(payload.keys()) if isinstance(payload, dict) else None,
                "has_issue": "issue" in payload if isinstance(payload, dict) else False,
            },
        )

        ticket_key = _extract_ticket_key(payload)

        debug_log(
            run_id="debug",
            hypothesis_id="C",
            location="jira_triage/webhook.py:jira_webhook",
            message="Ticket key extracted",
            data={
                "correlation_id": correlation_id,
                "ticket_key": ticket_key,
            },
        )

        webhook_logger.info(
            "Processing payload: correlation_id=%s ticket_key=%s payload_type=%s",
            correlation_id, ticket_key, type(payload).__name__
        )
        
        # Log payload size and structure (without sensitive data)
        payload_info = {}
        if isinstance(payload, dict):
            payload_info = {
                "keys": list(payload.keys()),
                "has_issue": "issue" in payload,
                "has_changelog": "changelog" in payload,
                "event_type": payload.get("eventType", "unknown"),
            }
        
        webhook_logger.debug(
            "Payload structure: correlation_id=%s info=%s",
            correlation_id, payload_info
        )
        
        open_requested = open or (isinstance(payload, dict) and _truthy(payload.get("open")))
        repo_requested = repo or (isinstance(payload, dict) and (payload.get("repo") or None)) or None
        logs_dir_requested = logs_dir or (isinstance(payload, dict) and (payload.get("logs_dir") or None)) or None
        
        webhook_logger.info(
            "Starting triage: correlation_id=%s ticket_key=%s open=%s repo=%s logs_dir=%s",
            correlation_id, ticket_key, open_requested, 
            repo_requested or "default", logs_dir_requested or "default"
        )
        
        # Check for duplicate processing before expensive triage operation
        duplicate_check_start = time.time()
        
        try:
            # Load config for duplicate detection
            config = load_config()
            
            # Fetch issue data for attachment checking
            webhook_logger.debug("Fetching issue data for duplicate detection: correlation_id=%s ticket_key=%s", 
                               correlation_id, ticket_key)
            
            issue_data = fetch_issue(config, ticket_key)
            
            # Check if we should skip processing
            should_skip, skip_reason = should_skip_processing(
                config=config,
                ticket_key=ticket_key,
                processing_mode="webhook",
                issue_data=issue_data
            )
            
            duplicate_check_duration = time.time() - duplicate_check_start
            
            if should_skip:
                webhook_logger.info(
                    "Skipping already processed ticket: correlation_id=%s ticket_key=%s reason='%s' check_duration=%.2fs",
                    correlation_id, ticket_key, skip_reason, duplicate_check_duration
                )
                
                return {
                    "ticket_id": ticket_key,
                    "status": "skipped",
                    "reason": skip_reason,
                    "correlation_id": correlation_id,
                    "duplicate_check_duration": duplicate_check_duration,
                    "message": f"Ticket {ticket_key} was already processed and skipped"
                }
            
            webhook_logger.debug(
                "Duplicate check passed: correlation_id=%s ticket_key=%s reason='%s' check_duration=%.2fs",
                correlation_id, ticket_key, skip_reason, duplicate_check_duration
            )
            
        except Exception as e:
            duplicate_check_duration = time.time() - duplicate_check_start
            webhook_logger.warning(
                "Duplicate detection failed, proceeding with processing: correlation_id=%s ticket_key=%s error='%s' duration=%.2fs",
                correlation_id, ticket_key, str(e), duplicate_check_duration
            )
            # Continue with processing on error (better to duplicate than miss)
        
        debug_log(
            run_id="debug",
            hypothesis_id="D,E",
            location="jira_triage/webhook.py:jira_webhook",
            message="About to call triage function",
            data={
                "correlation_id": correlation_id,
                "ticket_key": ticket_key,
                "open_cursor": open_requested,
                "repo": repo_requested,
                "logs_dir": logs_dir_requested,
            },
        )

        triage_start = time.time()
        result = triage(
            ticket_key,
            mode="webhook",
            open_cursor=open_requested,
            process_logs=False,
            cursor_analysis=True,
            attach=True,
            repo=repo_requested,
            logs_dir=logs_dir_requested,
        )
        triage_duration = time.time() - triage_start
        
        debug_log(
            run_id="debug",
            hypothesis_id="D,E",
            location="jira_triage/webhook.py:jira_webhook",
            message="Triage function completed",
            data={
                "correlation_id": correlation_id,
                "ticket_key": ticket_key,
                "duration": triage_duration,
                "output_dir": str(result.output_dir),
                "output_dir_exists": result.output_dir.exists() if hasattr(result, "output_dir") else False,
            },
        )

        webhook_logger.info(
            "Triage completed: correlation_id=%s ticket_key=%s duration=%.3fs output_dir=%s",
            correlation_id, ticket_key, triage_duration, result.output_dir
        )
        
    except TriageError as e:
        status = 400
        cause = e.__cause__
        if isinstance(cause, ConfigError):
            status = 500
        elif isinstance(cause, JiraError):
            status = 502
        
        webhook_logger.error(
            "Triage failed: correlation_id=%s ticket_key=%s error_type=%s status=%d error=%s",
            correlation_id, locals().get('ticket_key', 'unknown'), 
            type(cause).__name__ if cause else type(e).__name__,
            status, str(e)
        )
        raise HTTPException(status_code=status, detail=str(e)) from e
    except Exception as e:
        webhook_logger.error(
            "Unexpected error: correlation_id=%s ticket_key=%s error=%s",
            correlation_id, locals().get('ticket_key', 'unknown'), str(e),
            exc_info=True
        )
        raise HTTPException(status_code=500, detail="Internal server error") from e

    response_data = {
        "ticket_id": result.ticket_key,
        "repo_root": str(result.repo_root),
        "output_dir": str(result.output_dir),
        "cursor_context_path": str(result.context_path),
        "bundle_context_path": str(result.bundle_context_path),
        "cursor_analysis_txt_path": str(result.cursor_analysis_txt_path) if result.cursor_analysis_txt_path else None,
        "bundle_zip_path": str(result.bundle_zip_path) if result.bundle_zip_path else None,
        "correlation_id": correlation_id,
    }
    
    webhook_logger.info(
        "Response ready: correlation_id=%s ticket_key=%s files_created=%d",
        correlation_id, result.ticket_key,
        sum(1 for path in [result.context_path, result.bundle_context_path, 
                          result.cursor_analysis_txt_path, result.bundle_zip_path] if path)
    )
    
    return response_data


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

    # Initialize logging early
    try:
        from .logging_config import configure_all_logging
        loggers = configure_all_logging()
        
        # Log startup information
        app_logger = loggers["app"]
        app_logger.info("Starting jira-triage webhook server: host=%s port=%d reload=%s", 
                       args.host, args.port, args.reload)
        
        # Verify log directories exist and are writable
        from pathlib import Path
        project_root = Path(__file__).parent.parent
        logs_dir = project_root / "logs"
        logs_dir.mkdir(exist_ok=True)
        
        # Test write access
        test_file = logs_dir / ".startup_test"
        try:
            test_file.write_text("test")
            test_file.unlink()
            app_logger.info("Log directory verified: %s", logs_dir)
        except Exception as e:
            app_logger.error("Cannot write to log directory %s: %s", logs_dir, e)
            print(f"ERROR: Cannot write to log directory {logs_dir}: {e}", file=sys.stderr)
            return 1
            
    except Exception as e:
        print(f"Failed to initialize logging: {e}", file=sys.stderr)
        return 1

    try:
        import uvicorn  # type: ignore
    except Exception as e:
        print("Missing dependency: uvicorn. Reinstall with project dependencies.", file=sys.stderr)
        print(str(e), file=sys.stderr)
        return 1

    # Start the server
    try:
        uvicorn.run("jira_triage.webhook:app", host=args.host, port=args.port, reload=args.reload)
    except Exception as e:
        if 'app_logger' in locals():
            app_logger.error("Server startup failed: %s", e, exc_info=True)
        else:
            print(f"Server startup failed: {e}", file=sys.stderr)
        return 1
        
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

