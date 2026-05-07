from __future__ import annotations

import base64
import json
import ssl
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import Config
from .debug_log import debug_log
from .logging_config import setup_auth_logging, log_auth_attempt

# Initialize auth logger
auth_logger = setup_auth_logging()


class JiraError(RuntimeError):
    def __init__(self, message: str, *, error_type: str = "unknown", status_code: int | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code


def _classify_error(exception: Exception) -> tuple[str, str]:
    """Classify error type and return (error_type, description)."""
    if isinstance(exception, HTTPError):
        if exception.code == 401:
            return "auth_failed", f"Authentication failed (HTTP {exception.code})"
        elif exception.code == 403:
            return "auth_forbidden", f"Access forbidden (HTTP {exception.code})"
        elif exception.code in (404, 400):
            return "client_error", f"Client error (HTTP {exception.code})"
        elif 500 <= exception.code < 600:
            return "server_error", f"Server error (HTTP {exception.code})"
        else:
            return "http_error", f"HTTP error (HTTP {exception.code})"
    
    elif isinstance(exception, URLError):
        error_str = str(exception).lower()
        if "name resolution failed" in error_str or "nodename nor servname provided" in error_str:
            return "dns_error", "DNS resolution failed"
        elif "connection refused" in error_str:
            return "connection_refused", "Connection refused"
        elif "timeout" in error_str or "timed out" in error_str:
            return "timeout", "Connection timed out"
        elif "ssl" in error_str or "certificate" in error_str:
            return "ssl_error", "SSL/TLS error"
        elif "network is unreachable" in error_str:
            return "network_unreachable", "Network unreachable"
        else:
            return "network_error", f"Network error: {exception}"
    
    elif isinstance(exception, socket.error):
        return "socket_error", f"Socket error: {exception}"
    
    else:
        return "unknown_error", f"Unknown error: {type(exception).__name__}"


def _basic_auth_header(user: str, token: str) -> str:
    raw = f"{user}:{token}".encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"Basic {b64}"


def _auth_header(config: Config) -> str:
    mode = (config.jira_auth_mode or "basic").strip().lower()
    token = (config.jira_token or "").strip()
    
    # Log auth setup attempt
    log_auth_attempt(
        auth_logger,
        action="setup_auth",
        auth_mode=mode,
        jira_base_url=config.jira_base_url,
        token=token,  # Will be redacted by log_auth_attempt
        has_user=bool((config.jira_user or "").strip()),
        verify_ssl=bool(config.jira_verify_ssl),
    )
    
    if not token:
        auth_logger.error("Auth setup failed: no token provided")
        raise JiraError(
            "Jira REST credentials not configured. Set JIRA_TOKEN/JIRA_PAT (or rely on MCP-only by setting JIRA_SOURCE=mcp).",
            error_type="config_error"
        )
    if mode == "bearer":
        auth_logger.debug("Using Bearer token authentication")
        return f"Bearer {token}"
    user = (config.jira_user or "").strip()
    if not user:
        auth_logger.error("Auth setup failed: basic auth requires username")
        raise JiraError(
            "Jira REST basic auth requires JIRA_USER. If you are using a PAT, use bearer auth "
            "(set JIRA_AUTH_MODE=bearer or set JIRA_PAT).",
            error_type="config_error"
        )
    auth_logger.debug("Using Basic authentication with username: %s", user[:3] + "***" if len(user) > 3 else "***")
    return _basic_auth_header(user, token)


def _ssl_context(*, verify_ssl: bool) -> ssl.SSLContext | None:
    if verify_ssl:
        return None
    return ssl._create_unverified_context()


def _api_url(config: Config, api_version: int, path: str) -> str:
    base = config.jira_base_url.rstrip("/")
    p = path.lstrip("/")
    return f"{base}/rest/api/{api_version}/{p}"


def issue_endpoint(config: Config, ticket_key: str) -> str:
    key = quote(ticket_key)
    return _api_url(config, config.jira_api_version, f"issue/{key}")


def _probe_get(config: Config, url: str, *, auth_header: str) -> dict[str, Any]:
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": auth_header,
            "User-Agent": "jira-triage/0.1",
        },
        method="GET",
    )

    auth_logger.debug("Probe GET request: url=%s timeout=%ds verify_ssl=%s", 
                     url, config.http_timeout_seconds, config.jira_verify_ssl)

    try:
        with urlopen(
            req,
            timeout=config.http_timeout_seconds,
            context=_ssl_context(verify_ssl=config.jira_verify_ssl),
        ) as resp:
            status = getattr(resp, "status", None)
            hdrs = dict(resp.headers.items()) if resp.headers else {}
            
            # Log successful auth response
            auth_logger.info(
                "Probe GET success: url=%s status=%s seraph_reason=%s has_username=%s",
                url, status, hdrs.get("X-Seraph-LoginReason", "none"),
                bool(hdrs.get("X-AUSERNAME"))
            )
            
            return {
                "ok": True,
                "status_code": status,
                "x_seraph_loginreason": hdrs.get("X-Seraph-LoginReason"),
                "x_arequestid": hdrs.get("X-AREQUESTID"),
                "x_anodeid": hdrs.get("X-ANODEID"),
                "has_x_ausername": bool(hdrs.get("X-AUSERNAME")),
                "content_type": hdrs.get("Content-Type"),
                "www_authenticate": hdrs.get("WWW-Authenticate") or hdrs.get("Www-Authenticate"),
            }
    except HTTPError as e:
        hdrs = dict(e.headers.items()) if e.headers else {}
        error_type, error_desc = _classify_error(e)
        
        # Log HTTP error with classification
        auth_logger.warning(
            "Probe GET HTTP error: url=%s status=%s reason=%s error_type=%s seraph_reason=%s www_authenticate=%s",
            url, e.code, e.reason, error_type, 
            hdrs.get("X-Seraph-LoginReason", "none"),
            hdrs.get("WWW-Authenticate") or hdrs.get("Www-Authenticate", "none")
        )
        
        return {
            "ok": False,
            "status_code": e.code,
            "reason": e.reason,
            "error_type": error_type,
            "x_seraph_loginreason": hdrs.get("X-Seraph-LoginReason"),
            "x_arequestid": hdrs.get("X-AREQUESTID"),
            "x_anodeid": hdrs.get("X-ANODEID"),
            "has_x_ausername": bool(hdrs.get("X-AUSERNAME")),
            "content_type": hdrs.get("Content-Type"),
            "www_authenticate": hdrs.get("WWW-Authenticate") or hdrs.get("Www-Authenticate"),
            "final_url": getattr(e, "url", None) or getattr(e, "full_url", None),
        }
    except URLError as e:
        error_type, error_desc = _classify_error(e)
        
        # Log network error with classification
        auth_logger.error(
            "Probe GET network error: url=%s error_type=%s error=%s",
            url, error_type, str(e)
        )
        
        return {"ok": False, "error": str(e), "error_type": error_type}


def preflight_myself(config: Config) -> dict[str, Any]:
    """
    Small preflight to validate auth + basic Jira REST access.

    Notes:
    - Many instances allow `serverInfo` even anonymously; `myself` is a stricter signal.
    - This function returns a redacted dict (no user details).
    """
    auth_logger.info("Starting Jira preflight check: base_url=%s api_version=%d", 
                    config.jira_base_url, config.jira_api_version)
    
    auth_header = _auth_header(config)
    myself = _probe_get(config, _api_url(config, config.jira_api_version, "myself"), auth_header=auth_header)
    serverinfo = _probe_get(config, _api_url(config, config.jira_api_version, "serverInfo"), auth_header=auth_header)

    # Analyze preflight results
    myself_ok = myself.get("ok", False)
    serverinfo_ok = serverinfo.get("ok", False)
    
    if myself_ok and serverinfo_ok:
        auth_logger.info("Preflight successful: both /myself and /serverInfo accessible")
    elif myself_ok:
        auth_logger.warning("Preflight partial: /myself ok but /serverInfo failed (%s)", 
                          serverinfo.get("error_type", "unknown"))
    elif serverinfo_ok:
        auth_logger.warning("Preflight partial: /serverInfo ok but /myself failed (%s) - likely auth issue", 
                          myself.get("error_type", "unknown"))
    else:
        auth_logger.error("Preflight failed: both /myself and /serverInfo failed")
        auth_logger.error("  /myself error: %s", myself.get("error_type", "unknown"))
        auth_logger.error("  /serverInfo error: %s", serverinfo.get("error_type", "unknown"))

    # region agent log (no secrets) - keeping existing debug_log for compatibility
    try:
        scheme = auth_header.split(" ", 1)[0] if isinstance(auth_header, str) and auth_header else None
        debug_log(
            run_id="pre-fix",
            hypothesis_id="H32",
            location="jira_triage/jira_client.py:preflight_myself",
            message="Jira REST preflight result (redacted)",
            data={
                "jira_base_url": config.jira_base_url,
                "api_version": config.jira_api_version,
                "verify_ssl": bool(config.jira_verify_ssl),
                "timeout_seconds": config.http_timeout_seconds,
                "auth_mode": config.jira_auth_mode,
                "auth_scheme": scheme,
                "token_len": len((config.jira_token or "").strip()),
                "myself": myself,
                "serverinfo": serverinfo,
            },
        )
    except Exception:
        pass
    # endregion

    return {"myself": myself, "serverinfo": serverinfo}


def fetch_issue(config: Config, ticket_key: str) -> dict[str, Any]:
    url = issue_endpoint(config, ticket_key)
    auth_logger.info("Fetching issue: ticket=%s url=%s", ticket_key, url)
    
    auth_header = _auth_header(config)
    headers = {
        "Accept": "application/json",
        "Authorization": auth_header,
        "User-Agent": "jira-triage/0.1",
    }

    req = Request(url, headers=headers, method="GET")

    try:
        with urlopen(
            req,
            timeout=config.http_timeout_seconds,
            context=_ssl_context(verify_ssl=config.jira_verify_ssl),
        ) as resp:
            body = resp.read()
            auth_logger.info("Issue fetch successful: ticket=%s response_size=%d", 
                           ticket_key, len(body))
    except HTTPError as e:
        try:
            err_body = e.read()
        except Exception:
            err_body = b""
        snippet = err_body.decode("utf-8", errors="replace")[:2000]
        error_type, error_desc = _classify_error(e)

        auth_logger.error(
            "Issue fetch HTTP error: ticket=%s status=%s error_type=%s auth_mode=%s url=%s snippet=%s",
            ticket_key, e.code, error_type, config.jira_auth_mode, url, snippet[:500]
        )

        raise JiraError(
            f"Jira request failed ({e.code} {e.reason}) [auth_mode={config.jira_auth_mode}] for {url}: {snippet}",
            error_type=error_type,
            status_code=e.code
        ) from e
    except URLError as e:
        error_type, error_desc = _classify_error(e)
        
        auth_logger.error(
            "Issue fetch network error: ticket=%s error_type=%s url=%s error=%s",
            ticket_key, error_type, url, str(e)
        )
        
        raise JiraError(
            f"Jira request failed for {url}: {e}",
            error_type=error_type
        ) from e

    try:
        result = json.loads(body.decode("utf-8"))
        auth_logger.debug("Issue JSON parsed successfully: ticket=%s fields_count=%d", 
                         ticket_key, len(result.get("fields", {})))
        return result
    except json.JSONDecodeError as e:
        snippet = body.decode("utf-8", errors="replace")[:2000]
        auth_logger.error("Issue fetch JSON decode error: ticket=%s error=%s snippet=%s",
                         ticket_key, str(e), snippet[:500])
        raise JiraError(
            f"Jira response was not valid JSON for {url}: {snippet}",
            error_type="json_decode_error"
        ) from e

