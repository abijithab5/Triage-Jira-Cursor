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
    logs_source_ref: str | None = None,
    logs_copied_dir_name: str | None = None,
    logs_path: Path | None = None,
    logs_cleaned_path: Path | None = None,
    logs_summary_json_path: Path | None = None,
    logs_summary_path: Path | None = None,
    logs_summary_txt_path: Path | None = None,
    logs_error: str | None = None,
    repo_root: Path | None = None,
    cursor_context_path: Path | None = None,
    analysis_path: Path | None = None,
    analysis_txt_path: Path | None = None,
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
    if logs_source_ref:
        lines.append(f"- Merged Logs (CET): {logs_source_ref}")
    if logs_copied_dir_name:
        lines.append(f"- Copied logs (bundle): `{logs_copied_dir_name.rstrip('/')}/`")
    if logs_path is not None:
        lines.append(f"- Raw logs: `{logs_path.name}`")
    if logs_error:
        lines.append(f"- Logs fetch failed: {logs_error}")
    if not (logs_path or logs_source_ref or logs_copied_dir_name or logs_error):
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

    analysis_fill_path = analysis_txt_path or analysis_path
    if analysis_fill_path is not None:
        lines.append("## Analysis output")
        lines.append("")
        lines.append(f"- Fill in: `{analysis_fill_path.name}`")
        lines.append("")

    lines.append("## Bundle files")
    lines.append("")
    lines.append(f"- Issue JSON: `{issue_path.name}`")
    if logs_path is not None:
        lines.append(f"- Logs: `{logs_path.name}`")
    analysis_list_path = analysis_txt_path or analysis_path
    if analysis_list_path is not None:
        lines.append(f"- Analysis: `{analysis_list_path.name}`")
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
    lines.append("**Capacity and Role:** Act as a 20-year triage RDKB engineer.")
    lines.append("")
    
    lines.append("**Right Context:**")
    if logs_source_ref or logs_path is not None:
        if logs_source_ref:
            lines.append(f"- Logs: Browse the full logs folder at {logs_source_ref}")
        if logs_path is not None:
            lines.append(f"- Logs: View the individual log file at `{logs_path.name}`")
    else:
        lines.append("- Logs: No logs provided. Rely on the ticket description.")
    
    if repo_root is not None:
        lines.append("- Repo: You have access to the source code repository.")
    lines.append("- Ticket: Review the Jira description above for clues on the failure.")
    lines.append("")
    
    lines.append("**Instructions:**")
    lines.append("Diagnose the issue described in this Jira ticket using the provided logs.")
    if repo_root is not None:
        lines.append("Search the repo for relevant code matching error patterns found in the logs.")
    lines.append("")
    
    lines.append("**Statement of Action & Expected Output:**")
    lines.append("Produce a structured analysis containing:")
    lines.append("- A concise summary of the problem and user impact")
    lines.append("- Evidence from the logs (timestamps, error lines, stack traces)")
    lines.append("- Likely root causes and the most relevant code areas")
    lines.append("- A step-by-step investigation plan")
    lines.append("- Proposed fixes (including tests) and risks/rollout notes")
    lines.append("")
    
    lines.append("**Parameters:**")
    lines.append("Keep the entire analysis strictly within 300 words.")
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
    logs_source_ref: str | None = None,
    logs_copied_dir_name: str | None = None,
    logs_path: Path | None = None,
    logs_cleaned_path: Path | None = None,
    logs_summary_json_path: Path | None = None,
    logs_summary_path: Path | None = None,
    logs_summary_txt_path: Path | None = None,
    logs_error: str | None = None,
    analysis_path: Path | None = None,
    analysis_txt_path: Path | None = None,
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
    if logs_source_ref:
        lines.append(f"- Merged Logs (CET): {logs_source_ref}")
    if logs_copied_dir_name:
        lines.append(f"- Copied logs (bundle): `{_rel(ticket_dir / logs_copied_dir_name).rstrip('/')}/`")
    if logs_path is not None:
        lines.append(f"- Raw logs: `{_rel(logs_path)}`")
    if logs_error:
        lines.append(f"- Logs error: {logs_error}")
    analysis_write_path = analysis_txt_path or analysis_path
    if analysis_write_path is not None:
        lines.append(f"- Write analysis here: `{_rel(analysis_write_path)}`")
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
    lines.append("**Capacity and Role:** Act as a 20-year triage RDKB engineer.")
    lines.append("")
    
    lines.append("**Right Context:**")
    lines.append("- Ticket: Review the Jira description above for clues on the failure.")
    if logs_source_ref or logs_path is not None:
        if logs_source_ref:
            lines.append(f"- Logs: Browse the full logs folder at {logs_source_ref}")
        if logs_path is not None:
            lines.append(f"- Logs: View the individual log file at `{_rel(logs_path)}`")
    else:
        lines.append("- Logs: No logs provided. Rely on the ticket description.")
        
    lines.append("- Repo: You have access to the source code repository.")
    lines.append("")
    
    lines.append("**Instructions:**")
    lines.append("Diagnose the issue described in this Jira ticket using the provided logs.")
    lines.append("Search the repo for relevant code matching error patterns found in the logs.")
    lines.append("")
    
    lines.append("**Statement of Action & Expected Output:**")
    lines.append("Produce a structured analysis containing:")
    lines.append("- A concise summary of the problem and user impact")
    lines.append("- Evidence from the logs (timestamps, error lines, stack traces)")
    lines.append("- Likely root causes and the most relevant code areas")
    lines.append("- A step-by-step investigation plan")
    lines.append("- Proposed fixes (including tests) and risks/rollout notes")
    if analysis_write_path is not None:
        lines.append(f"Write this analysis to `{_rel(analysis_write_path)}`.")
    lines.append("")
    
    lines.append("**Parameters:**")
    lines.append("Keep the entire analysis strictly within 300 words.")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"

