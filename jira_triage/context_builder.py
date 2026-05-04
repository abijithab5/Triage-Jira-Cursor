from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _dig(obj: Any, *path: str) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _format_description(desc: Any) -> str:
    if desc is None:
        return ""
    if isinstance(desc, str):
        return desc
    try:
        return json.dumps(desc, ensure_ascii=False, indent=2)
    except Exception:
        return str(desc)


def build_context_markdown(
    *,
    ticket_key: str,
    jira_base_url: str,
    issue: dict[str, Any],
    issue_path: Path,
    logs_path: Path | None = None,
    logs_cleaned_path: Path | None = None,
    logs_summary_json_path: Path | None = None,
    logs_summary_path: Path | None = None,
    logs_error: str | None = None,
    repo_root: Path | None = None,
    cursor_context_path: Path | None = None,
    analysis_path: Path | None = None,
    suggested_paths: list[dict[str, Any]] | None = None,
    jira_source_used: str | None = None,
) -> str:
    link = f"{jira_base_url.rstrip('/')}/browse/{ticket_key}"

    fields = issue.get("fields") if isinstance(issue, dict) else {}
    summary = _dig(fields, "summary") or ""
    status = _dig(fields, "status", "name") or ""

    assignee_obj = _dig(fields, "assignee")
    assignee = ""
    if isinstance(assignee_obj, dict):
        assignee = (
            assignee_obj.get("displayName")
            or assignee_obj.get("name")
            or assignee_obj.get("emailAddress")
            or ""
        )

    description = _format_description(_dig(fields, "description"))

    lines: list[str] = []
    lines.append(f"# {ticket_key}: {summary}".rstrip())
    lines.append("")
    lines.append("## Ticket")
    lines.append(f"- Link: {link}")
    if jira_source_used:
        lines.append(f"- Jira source: {jira_source_used}")
    if status:
        lines.append(f"- Status: {status}")
    if assignee:
        lines.append(f"- Assignee: {assignee}")
    lines.append("")

    lines.append("## Description")
    lines.append("")
    if description:
        if isinstance(_dig(fields, "description"), str):
            lines.append(description)
        else:
            lines.append("```json")
            lines.append(description)
            lines.append("```")
    else:
        lines.append("_No description provided._")
    lines.append("")

    lines.append("## Logs")
    lines.append("")
    if logs_path is not None:
        lines.append(f"- Raw logs: `{logs_path.name}`")
        if logs_cleaned_path is not None:
            lines.append(f"- Cleaned logs: `{logs_cleaned_path.name}`")
        if logs_summary_json_path is not None:
            lines.append(f"- Logs summary (JSON): `{logs_summary_json_path.name}`")
        if logs_summary_path is not None:
            lines.append(f"- Logs summary: `{logs_summary_path.name}`")
    elif logs_error:
        lines.append(f"- Logs fetch failed: {logs_error}")
    else:
        lines.append("- Logs fetch not configured (set `LOG_API_URL` to enable).")
    lines.append("")

    if suggested_paths:
        lines.append("## Suggested repo paths")
        lines.append("")
        for item in suggested_paths[:20]:
            p = item.get("path")
            score = item.get("score")
            if isinstance(p, str) and p.strip():
                s = f"- `{p}`"
                if isinstance(score, int):
                    s += f" (score={score})"
                lines.append(s)
        lines.append("")

    if analysis_path is not None:
        lines.append("## Analysis output")
        lines.append("")
        lines.append(f"- Fill in: `{analysis_path.name}`")
        lines.append("")

    lines.append("## Bundle files")
    lines.append("")
    lines.append(f"- Issue JSON: `{issue_path.name}`")
    if logs_path is not None:
        lines.append(f"- Logs: `{logs_path.name}`")
    if logs_cleaned_path is not None:
        lines.append(f"- Logs cleaned: `{logs_cleaned_path.name}`")
    if logs_summary_path is not None:
        lines.append(f"- Logs summary: `{logs_summary_path.name}`")
    if logs_summary_json_path is not None:
        lines.append(f"- Logs summary (JSON): `{logs_summary_json_path.name}`")
    if analysis_path is not None:
        lines.append(f"- Analysis: `{analysis_path.name}`")
    lines.append("")

    if repo_root is not None and cursor_context_path is not None:
        lines.append("## Cursor context")
        lines.append("")
        try:
            rel = cursor_context_path.relative_to(repo_root)
            lines.append(f"- `.cursor` context: `{rel.as_posix()}`")
        except Exception:
            lines.append(f"- `.cursor` context: `{cursor_context_path}`")
        lines.append("")

    lines.append("## Analysis prompt (Cursor)")
    lines.append("")
    lines.append("Using the ticket details above (and logs if present), produce:")
    lines.append("- A concise summary of the problem and user impact")
    lines.append("- Likely root causes and the most relevant code areas")
    lines.append("- A step-by-step investigation plan")
    lines.append("- Proposed fixes (including tests) and risks/rollout notes")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_cursor_ticket_markdown(
    *,
    repo_root: Path,
    ticket_key: str,
    jira_base_url: str,
    issue: dict[str, Any],
    ticket_dir: Path,
    issue_path: Path,
    jira_source_used: str | None = None,
    logs_path: Path | None = None,
    logs_cleaned_path: Path | None = None,
    logs_summary_json_path: Path | None = None,
    logs_summary_path: Path | None = None,
    logs_error: str | None = None,
    analysis_path: Path | None = None,
    suggested_paths: list[dict[str, Any]] | None = None,
) -> str:
    """
    Cursor-facing context file written to `.cursor/context/TICKET.md` inside the repo.
    """
    fields = issue.get("fields") if isinstance(issue, dict) else {}
    summary = _dig(fields, "summary") or ""
    status = _dig(fields, "status", "name") or ""
    link = f"{jira_base_url.rstrip('/')}/browse/{ticket_key}"

    def _rel(p: Path) -> str:
        try:
            return p.relative_to(repo_root).as_posix()
        except Exception:
            return str(p)

    lines: list[str] = []
    lines.append(f"# {ticket_key}: {summary}".rstrip())
    lines.append("")
    lines.append("## Ticket")
    lines.append(f"- Link: {link}")
    if jira_source_used:
        lines.append(f"- Jira source: {jira_source_used}")
    if status:
        lines.append(f"- Status: {status}")
    lines.append("")

    lines.append("## Bundle (generated artifacts)")
    lines.append("")
    lines.append(f"- Bundle dir: `{_rel(ticket_dir)}`")
    lines.append(f"- Issue JSON: `{_rel(issue_path)}`")
    if logs_path is not None:
        lines.append(f"- Raw logs: `{_rel(logs_path)}`")
    if logs_cleaned_path is not None:
        lines.append(f"- Cleaned logs: `{_rel(logs_cleaned_path)}`")
    if logs_summary_json_path is not None:
        lines.append(f"- Logs summary (JSON): `{_rel(logs_summary_json_path)}`")
    if logs_summary_path is not None:
        lines.append(f"- Logs summary: `{_rel(logs_summary_path)}`")
    if logs_error:
        lines.append(f"- Logs error: {logs_error}")
    if analysis_path is not None:
        lines.append(f"- Write analysis here: `{_rel(analysis_path)}`")
    lines.append("")

    if suggested_paths:
        lines.append("## Suggested repo paths to inspect")
        lines.append("")
        for item in suggested_paths[:25]:
            p = item.get("path")
            if isinstance(p, str) and p.strip():
                lines.append(f"- `{p}`")
        lines.append("")

    lines.append("## What to do in Cursor")
    lines.append("")
    lines.append("Goal: diagnose and fix the issue described by the Jira ticket, using the attached logs and repo context.")
    lines.append("")
    lines.append("Deliverables:")
    if analysis_path is not None:
        lines.append(f"- Update `{_rel(analysis_path)}` with: summary, evidence (log snippets), root cause, fix plan, test plan, rollout notes.")
    else:
        lines.append("- Produce: summary, evidence (log snippets), root cause, fix plan, test plan, rollout notes.")
    lines.append("")
    lines.append("Investigation hints:")
    lines.append("- Start from the log summary and error samples to identify the failing component and call path.")
    lines.append("- Search for exception class names / endpoints / correlation IDs in the repo.")
    lines.append("- Narrow to the minimal set of files needed for a safe fix + tests.")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"

