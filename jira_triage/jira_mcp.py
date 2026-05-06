from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .debug_log import debug_log
from .jira_client import JiraError
from .mcp import McpError, StdioMcpClient, load_cursor_mcp_server


@dataclass(frozen=True)
class JiraMcpResult:
    issue: dict[str, Any]
    server_info: dict[str, Any] | None = None


def _extract_text_from_calltool_result(result: dict[str, Any]) -> str:
    """
    Extract a tool output string from a CallToolResult.
    The Atlassian MCP server (FastMCP) commonly returns a JSON string.
    """
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        v = structured.get("result")
        if isinstance(v, str) and v.strip():
            return v

    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    return text

    raise JiraError(f"Unexpected MCP tool result shape (no text payload): {result}")


def fetch_issue_via_mcp(config: Config, ticket_key: str) -> JiraMcpResult:
    server_name = (config.jira_mcp_server or "").strip() or "mcp-atlassian"
    cursor_cfg_path = Path(config.cursor_mcp_config_path).expanduser() if config.cursor_mcp_config_path else None
    server = load_cursor_mcp_server(server_name, cursor_mcp_config_path=cursor_cfg_path)
    if server is None:
        raise JiraError(
            f"MCP server {server_name!r} not found in Cursor config. "
            f"Set JIRA_MCP_SERVER_NAME or configure the server in ~/.cursor/mcp.json."
        )

    try:
        with StdioMcpClient(server) as client:
            server_info = client.initialize()
            call_result = client.request(
                "tools/call",
                params={
                    "name": "jira_get_issue",
                    "arguments": {
                        "issue_key": ticket_key,
                        # Defaults on the server are good; keep this explicit for stability.
                        "fields": "assignee,description,status,summary,updated,reporter,created,labels,priority,issuetype",
                        "comment_limit": 10,
                        "update_history": True,
                    },
                },
            )

        # region agent log (no secrets)
        debug_log(
            run_id="pre-fix",
            hypothesis_id="H8",
            location="jira_triage/jira_mcp.py:fetch_issue_via_mcp",
            message="MCP jira_get_issue call completed (shape only)",
            data={
                "server_name": server_name,
                "ticket_key": ticket_key,
                "server_info_keys": sorted(list(server_info.keys())) if isinstance(server_info, dict) else [],
                "call_result_keys": sorted(list(call_result.keys())) if isinstance(call_result, dict) else [],
                "call_is_error": bool(call_result.get("isError") is True) if isinstance(call_result, dict) else None,
            },
        )
        # endregion

        if call_result.get("isError") is True:
            text = _extract_text_from_calltool_result(call_result)
            raise JiraError(f"MCP jira_get_issue returned an error: {text}")

        text = _extract_text_from_calltool_result(call_result)
        try:
            issue = json.loads(text)
        except Exception as e:
            raise JiraError(f"MCP jira_get_issue returned non-JSON text: {text[:2000]}") from e
        if not isinstance(issue, dict):
            raise JiraError("MCP jira_get_issue returned JSON but not an object")
        return JiraMcpResult(issue=issue, server_info=server_info)
    except McpError as e:
        raise JiraError(str(e)) from e

