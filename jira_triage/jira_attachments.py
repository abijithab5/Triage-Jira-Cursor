from __future__ import annotations

import json
import mimetypes
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import Config
from .debug_log import debug_log
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
            j = json.loads(txt)
        return AttachmentUploadResult(ok=True, status_code=status, response_json=j)
    except Exception:
        return AttachmentUploadResult(ok=True, status_code=status, response_json=txt)


@dataclass(frozen=True)
class JiraAttachment:
    """Represents a Jira attachment."""
    id: str
    filename: str
    size: int
    mime_type: str
    created: datetime
    author_display_name: str
    content_url: str
    self_url: str


def generate_our_attachment_filename(ticket_key: str, timestamp: Optional[datetime] = None) -> str:
    """Generate a consistent filename for our analysis bundle attachments.
    
    Args:
        ticket_key: Jira ticket key (e.g., 'PROJ-123')
        timestamp: Optional timestamp to include in filename
        
    Returns:
        Filename string like 'jira-triage-analysis-PROJ-123-20241208-143022.zip'
    """
    if timestamp is None:
        timestamp = datetime.now()
    
    # Format: jira-triage-analysis-{ticket_key}-{yyyymmdd-hhmmss}.zip
    timestamp_str = timestamp.strftime("%Y%m%d-%H%M%S")
    return f"jira-triage-analysis-{ticket_key}-{timestamp_str}.zip"


def is_our_attachment_filename(filename: str) -> bool:
    """Check if a filename matches our analysis bundle pattern.
    
    Args:
        filename: Attachment filename to check
        
    Returns:
        True if filename matches our pattern, False otherwise
    """
    # Pattern: jira-triage-analysis-{ticket_key}-{timestamp}.zip
    pattern = r'^jira-triage-analysis-.+-\d{8}-\d{6}\.zip$'
    return bool(re.match(pattern, filename))


def parse_issue_attachments(issue_data: Dict[str, Any]) -> List[JiraAttachment]:
    """Parse attachment data from Jira issue JSON.
    
    Args:
        issue_data: Full Jira issue JSON response
        
    Returns:
        List of JiraAttachment objects
    """
    attachments = []
    
    try:
        attachment_list = issue_data.get("fields", {}).get("attachment", [])
        if not isinstance(attachment_list, list):
            debug_log(
            run_id="debug",
            hypothesis_id="H1",
            location="jira_triage/jira_attachments.py:parse_issue_attachments",
            message="invalid_attachment_field",
            data={
                "attachment_field_type": type(attachment_list).__name__,
                "attachment_field": attachment_list
            }
        )
            return attachments
            
        for att_data in attachment_list:
            try:
                # Parse creation date
                created_str = att_data.get("created", "")
                created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
                
                # Extract author name
                author = att_data.get("author", {})
                author_name = author.get("displayName", "Unknown")
                
                attachment = JiraAttachment(
                    id=att_data["id"],
                    filename=att_data["filename"],
                    size=int(att_data.get("size", 0)),
                    mime_type=att_data.get("mimeType", "application/octet-stream"),
                    created=created,
                    author_display_name=author_name,
                    content_url=att_data.get("content", ""),
                    self_url=att_data.get("self", "")
                )
                attachments.append(attachment)
                
            except (KeyError, ValueError, TypeError) as e:
                debug_log(
                    run_id="debug",
                    hypothesis_id="H1",
                    location="jira_triage/jira_attachments.py:parse_issue_attachments",
                    message="attachment_parse_error",
                    data={
                        "error": str(e),
                        "attachment_data": att_data
                    }
                )
                continue
                
    except Exception as e:
        debug_log(
            run_id="debug",
            hypothesis_id="H1",
            location="jira_triage/jira_attachments.py:parse_issue_attachments",
            message="issue_attachment_parse_error",
            data={
                "error": str(e),
                "issue_key": issue_data.get("key", "unknown")
            }
        )
    
    return attachments


def find_our_attachments(attachments: List[JiraAttachment]) -> List[JiraAttachment]:
    """Find our analysis bundle attachments from a list of attachments.
    
    Args:
        attachments: List of JiraAttachment objects
        
    Returns:
        List of our analysis bundle attachments
    """
    our_attachments = []
    
    for attachment in attachments:
        if is_our_attachment_filename(attachment.filename):
            our_attachments.append(attachment)
    
    debug_log(
        run_id="debug",
        hypothesis_id="H1",
        location="jira_triage/jira_attachments.py:find_our_attachments",
        message="our_attachments_found",
        data={
            "total_attachments": len(attachments),
            "our_attachments": len(our_attachments),
            "our_filenames": [att.filename for att in our_attachments]
        }
    )
    
    return our_attachments


def check_our_attachment_exists(issue_data: Dict[str, Any]) -> bool:
    """Check if our analysis bundle attachment exists on a Jira issue.
    
    Args:
        issue_data: Full Jira issue JSON response
        
    Returns:
        True if our attachment exists, False otherwise
    """
    attachments = parse_issue_attachments(issue_data)
    our_attachments = find_our_attachments(attachments)
    
    exists = len(our_attachments) > 0
    
    debug_log(
        run_id="debug",
        hypothesis_id="H1",
        location="jira_triage/jira_attachments.py:check_our_attachment_exists",
        message="attachment_existence_check",
        data={
            "ticket_key": issue_data.get("key", "unknown"),
            "total_attachments": len(attachments),
            "our_attachments_found": len(our_attachments),
            "exists": exists
        }
    )
    
    return exists


def get_our_attachment_info(issue_data: Dict[str, Any]) -> Optional[JiraAttachment]:
    """Get info about our latest analysis bundle attachment.
    
    Args:
        issue_data: Full Jira issue JSON response
        
    Returns:
        JiraAttachment object for our latest attachment, or None if not found
    """
    attachments = parse_issue_attachments(issue_data)
    our_attachments = find_our_attachments(attachments)
    
    if not our_attachments:
        return None
    
    # Return the most recent attachment (by creation date)
    latest = max(our_attachments, key=lambda att: att.created)
    
    debug_log(
        run_id="debug",
        hypothesis_id="H1",
        location="jira_triage/jira_attachments.py:get_our_attachment_info",
        message="our_attachment_info",
        data={
            "ticket_key": issue_data.get("key", "unknown"),
            "latest_attachment_id": latest.id,
            "latest_filename": latest.filename,
            "latest_created": latest.created.isoformat(),
            "author": latest.author_display_name
        }
    )
    
    return latest


def enhanced_upload_issue_attachment(config: Config, 
                                   ticket_key: str, 
                                   file_path: Path,
                                   custom_filename: Optional[str] = None) -> AttachmentUploadResult:
    """Upload an attachment with enhanced metadata and consistent naming.
    
    Args:
        config: Jira configuration
        ticket_key: Jira ticket key
        file_path: Path to file to upload
        custom_filename: Optional custom filename to use instead of file_path.name
        
    Returns:
        AttachmentUploadResult with upload status
    """
    # Use custom filename if provided, otherwise use original upload function
    if custom_filename:
        # Temporarily rename file for upload
        temp_path = file_path.parent / custom_filename
        try:
            # Create a copy with the desired name
            temp_path.write_bytes(file_path.read_bytes())
            result = upload_issue_attachment(config, ticket_key, temp_path)
        finally:
            # Clean up temporary file
            if temp_path.exists():
                temp_path.unlink()
        
        debug_log(
            run_id="debug",
            hypothesis_id="H1",
            location="jira_triage/jira_attachments.py:enhanced_upload_issue_attachment",
            message="enhanced_attachment_upload",
            data={
                "ticket_key": ticket_key,
                "original_filename": file_path.name,
                "custom_filename": custom_filename,
                "upload_success": result.ok,
                "status_code": result.status_code
            }
        )
        
        return result
    else:
        return upload_issue_attachment(config, ticket_key, file_path)

