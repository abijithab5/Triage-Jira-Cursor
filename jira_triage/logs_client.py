from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .config import Config


@dataclass(frozen=True)
class LogsResult:
    ok: bool
    url: str
    status_code: int | None = None
    content_type: str | None = None
    body: bytes | None = None
    parsed_json: Any | None = None
    text: str | None = None
    error: str | None = None


def _ssl_context(*, verify_ssl: bool) -> ssl.SSLContext | None:
    if verify_ssl:
        return None
    return ssl._create_unverified_context()


def _with_query_param(url: str, name: str, value: str) -> str:
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q[name] = value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def fetch_logs(config: Config, ticket_key: str) -> LogsResult | None:
    if not config.log_api_url:
        return None

    method = (config.log_api_method or "GET").strip().upper()
    verify_ssl = config.jira_verify_ssl if config.log_api_verify_ssl is None else config.log_api_verify_ssl

    headers = {
        "Accept": "*/*",
        "User-Agent": "jira-triage/0.1",
    }

    data: bytes | None = None
    if method == "GET":
        url = _with_query_param(config.log_api_url, config.log_api_param_name, ticket_key)
    elif method == "POST":
        url = config.log_api_url
        payload = {config.log_api_param_name: ticket_key}
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        return LogsResult(ok=False, url=config.log_api_url, error=f"Unsupported LOG_API_METHOD: {method}")

    req = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(
            req,
            timeout=config.http_timeout_seconds,
            context=_ssl_context(verify_ssl=verify_ssl),
        ) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type")
            status = getattr(resp, "status", None)
    except HTTPError as e:
        try:
            err_body = e.read()
        except Exception:
            err_body = b""
        snippet = err_body.decode("utf-8", errors="replace")[:2000]
        return LogsResult(
            ok=False,
            url=url,
            status_code=e.code,
            content_type=e.headers.get("Content-Type") if e.headers else None,
            body=err_body,
            text=snippet,
            error=f"Logs request failed ({e.code} {e.reason}) for {url}: {snippet}",
        )
    except URLError as e:
        return LogsResult(ok=False, url=url, error=f"Logs request failed for {url}: {e}")

    parsed_json: Any | None = None
    text: str | None = None
    if body is not None:
        try:
            parsed_json = json.loads(body.decode("utf-8"))
        except Exception:
            text = body.decode("utf-8", errors="replace")

    return LogsResult(
        ok=True,
        url=url,
        status_code=status,
        content_type=content_type,
        body=body,
        parsed_json=parsed_json,
        text=text,
    )

