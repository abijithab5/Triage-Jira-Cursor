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
        raise JiraError(
            "Jira REST basic auth requires JIRA_USER. If you are using a PAT, use bearer auth "
            "(set JIRA_AUTH_MODE=bearer or set JIRA_PAT)."
        )
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
                "www_authenticate": hdrs.get("WWW-Authenticate") or hdrs.get("Www-Authenticate"),
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
            "www_authenticate": hdrs.get("WWW-Authenticate") or hdrs.get("Www-Authenticate"),
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

    # region agent log (no secrets)
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
    except HTTPError as e:
        try:
            err_body = e.read()
        except Exception:
            err_body = b""
        snippet = err_body.decode("utf-8", errors="replace")[:2000]

        raise JiraError(
            f"Jira request failed ({e.code} {e.reason}) [auth_mode={config.jira_auth_mode}] for {url}: {snippet}"
        ) from e
    except URLError as e:
        raise JiraError(f"Jira request failed for {url}: {e}") from e

    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as e:
        snippet = body.decode("utf-8", errors="replace")[:2000]
        raise JiraError(f"Jira response was not valid JSON for {url}: {snippet}") from e

