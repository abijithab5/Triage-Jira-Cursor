from __future__ import annotations

import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import Config
from .jira_client import _api_url, _auth_header, _ssl_context


@dataclass(frozen=True)
class AttachmentUploadResult:
    ok: bool
    status_code: int | None = None
    response_json: Any | None = None
    error: str | None = None


def _multipart_form_data(file_field: str, file_path: Path, *, boundary: str) -> tuple[bytes, str]:
    filename = file_path.name
    ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()

    lines: list[bytes] = []
    b = boundary.encode("utf-8")
    crlf = b"\r\n"

    lines.append(b"--" + b)
    lines.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'.encode("utf-8")
    )
    lines.append(f"Content-Type: {ctype}".encode("utf-8"))
    lines.append(b"")
    lines.append(file_bytes)
    lines.append(b"--" + b + b"--")
    lines.append(b"")

    body = crlf.join(lines)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def upload_issue_attachment(config: Config, ticket_key: str, file_path: Path) -> AttachmentUploadResult:
    """
    Upload an attachment to a Jira issue via REST API.
    Requires Jira REST credentials (basic or bearer).
    """
    key = quote(ticket_key)
    url = _api_url(config, config.jira_api_version, f"issue/{key}/attachments")
    auth = _auth_header(config)

    boundary = "----jira-triage-" + uuid.uuid4().hex
    body, content_type = _multipart_form_data("file", file_path, boundary=boundary)

    headers = {
        "Accept": "application/json",
        "Authorization": auth,
        "User-Agent": "jira-triage/0.2",
        "X-Atlassian-Token": "no-check",
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
    }

    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(
            req,
            timeout=config.http_timeout_seconds,
            context=_ssl_context(verify_ssl=config.jira_verify_ssl),
        ) as resp:
            status = getattr(resp, "status", None)
            rb = resp.read()
    except HTTPError as e:
        try:
            err_body = e.read()
        except Exception:
            err_body = b""
        snippet = err_body.decode("utf-8", errors="replace")[:2000]
        return AttachmentUploadResult(
            ok=False,
            status_code=e.code,
            error=f"Attachment upload failed ({e.code} {e.reason}) for {url}: {snippet}",
        )
    except URLError as e:
        return AttachmentUploadResult(ok=False, error=f"Attachment upload failed for {url}: {e}")

    try:
        txt = rb.decode("utf-8", errors="replace")
    except Exception:
        txt = ""

    try:
        j = None
        if txt.strip():
            import json

            j = json.loads(txt)
        return AttachmentUploadResult(ok=True, status_code=status, response_json=j)
    except Exception:
        return AttachmentUploadResult(ok=True, status_code=status, response_json=txt)

