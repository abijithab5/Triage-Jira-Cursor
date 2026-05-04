from __future__ import annotations

import base64
import json
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import Config
from .debug_log import debug_log


class JiraError(RuntimeError):
    pass


def _basic_auth_header(user: str, token: str) -> str:
    raw = f"{user}:{token}".encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"Basic {b64}"


def _auth_header(config: Config) -> str:
    mode = (config.jira_auth_mode or "basic").strip().lower()
    token = (config.jira_token or "").strip()
    if not token:
        raise JiraError(
            "Jira REST credentials not configured. Set JIRA_TOKEN/JIRA_PAT (or rely on MCP-only by setting JIRA_SOURCE=mcp)."
        )
    if mode == "bearer":
        return f"Bearer {token}"
    user = (config.jira_user or "").strip()
    if not user:
        raise JiraError("Jira REST basic auth requires JIRA_USER.")
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

    try:
        with urlopen(
            req,
            timeout=config.http_timeout_seconds,
            context=_ssl_context(verify_ssl=config.jira_verify_ssl),
        ) as resp:
            status = getattr(resp, "status", None)
            hdrs = dict(resp.headers.items()) if resp.headers else {}
            return {
                "ok": True,
                "status_code": status,
                "x_seraph_loginreason": hdrs.get("X-Seraph-LoginReason"),
                "x_arequestid": hdrs.get("X-AREQUESTID"),
                "x_anodeid": hdrs.get("X-ANODEID"),
                "has_x_ausername": bool(hdrs.get("X-AUSERNAME")),
                "content_type": hdrs.get("Content-Type"),
            }
    except HTTPError as e:
        hdrs = dict(e.headers.items()) if e.headers else {}
        return {
            "ok": False,
            "status_code": e.code,
            "reason": e.reason,
            "x_seraph_loginreason": hdrs.get("X-Seraph-LoginReason"),
            "x_arequestid": hdrs.get("X-AREQUESTID"),
            "x_anodeid": hdrs.get("X-ANODEID"),
            "has_x_ausername": bool(hdrs.get("X-AUSERNAME")),
            "content_type": hdrs.get("Content-Type"),
            "final_url": getattr(e, "url", None) or getattr(e, "full_url", None),
        }
    except URLError as e:
        return {"ok": False, "error": str(e)}


def preflight_myself(config: Config) -> dict[str, Any]:
    """
    Small preflight to validate auth + basic Jira REST access.

    Notes:
    - Many instances allow `serverInfo` even anonymously; `myself` is a stricter signal.
    - This function returns a redacted dict (no user details).
    """
    auth_header = _auth_header(config)
    myself = _probe_get(config, _api_url(config, config.jira_api_version, "myself"), auth_header=auth_header)
    serverinfo = _probe_get(config, _api_url(config, config.jira_api_version, "serverInfo"), auth_header=auth_header)

    # region agent log
    debug_log(
        run_id="pre-fix",
        hypothesis_id="H6",
        location="jira_triage/jira_client.py:preflight_myself",
        message="Preflight results for /myself and /serverInfo (redacted)",
        data={
            "api_version": config.jira_api_version,
            "auth_mode": config.jira_auth_mode,
            "myself": myself,
            "serverinfo": serverinfo,
        },
    )
    # endregion

    return {"myself": myself, "serverinfo": serverinfo}


def fetch_issue(config: Config, ticket_key: str) -> dict[str, Any]:
    url = issue_endpoint(config, ticket_key)
    auth_header = _auth_header(config)
    headers = {
        "Accept": "application/json",
        "Authorization": auth_header,
        "User-Agent": "jira-triage/0.1",
    }

    # region agent log
    debug_log(
        run_id="pre-fix",
        hypothesis_id="H2",
        location="jira_triage/jira_client.py:fetch_issue",
        message="About to call Jira issue endpoint",
        data={
            "url": url,
            "ticket_key": ticket_key,
            "auth_mode": config.jira_auth_mode,
            "auth_header_prefix": (auth_header.split(" ", 1)[0] if auth_header else ""),
            "jira_verify_ssl": config.jira_verify_ssl,
            "timeout_seconds": config.http_timeout_seconds,
        },
    )
    # endregion

    req = Request(url, headers=headers, method="GET")

    try:
        with urlopen(
            req,
            timeout=config.http_timeout_seconds,
            context=_ssl_context(verify_ssl=config.jira_verify_ssl),
        ) as resp:
            body = resp.read()
    except HTTPError as e:
        try:
            err_body = e.read()
        except Exception:
            err_body = b""
        snippet = err_body.decode("utf-8", errors="replace")[:2000]

        # region agent log
        hdrs = dict(e.headers.items()) if e.headers else {}
        debug_log(
            run_id="pre-fix",
            hypothesis_id="H3",
            location="jira_triage/jira_client.py:fetch_issue",
            message="Jira HTTPError",
            data={
                "status_code": e.code,
                "reason": e.reason,
                "url": url,
                "final_url": getattr(e, "url", None) or getattr(e, "full_url", None),
                "auth_mode": config.jira_auth_mode,
                "x_seraph_loginreason": hdrs.get("X-Seraph-LoginReason"),
                "x_arequestid": hdrs.get("X-AREQUESTID"),
                "x_anodeid": hdrs.get("X-ANODEID"),
                "content_type": hdrs.get("Content-Type"),
                "content_length": hdrs.get("Content-Length"),
                "has_x_ausername": bool(hdrs.get("X-AUSERNAME")),
                "has_set_cookie": "Set-Cookie" in hdrs,
                "error_body_len": len(err_body),
            },
        )
        # endregion

        if e.code in (401, 403):
            # region agent log
            debug_log(
                run_id="pre-fix",
                hypothesis_id="H5",
                location="jira_triage/jira_client.py:fetch_issue",
                message="Auth/permissions probes after 401/403",
                data={
                    "myself_v2": _probe_get(config, _api_url(config, 2, "myself"), auth_header=auth_header),
                    "serverinfo_v2": _probe_get(config, _api_url(config, 2, "serverInfo"), auth_header=auth_header),
                },
            )
            # endregion

        raise JiraError(
            f"Jira request failed ({e.code} {e.reason}) [auth_mode={config.jira_auth_mode}] for {url}: {snippet}"
        ) from e
    except URLError as e:
        # region agent log
        debug_log(
            run_id="pre-fix",
            hypothesis_id="H4",
            location="jira_triage/jira_client.py:fetch_issue",
            message="Jira URLError",
            data={"url": url, "auth_mode": config.jira_auth_mode, "error": str(e)},
        )
        # endregion
        raise JiraError(f"Jira request failed for {url}: {e}") from e

    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as e:
        snippet = body.decode("utf-8", errors="replace")[:2000]
        raise JiraError(f"Jira response was not valid JSON for {url}: {snippet}") from e

