from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from typing import Sequence

from .core import TriageError, triage
from .debug_log import debug_log


_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._\-]{10,}", flags=re.I)
_AUTHZ_RE = re.compile(r"(Authorization\s*:\s*)(\S+)", flags=re.I)


def _redact_high_risk_secrets(s: str) -> str:
    s = _BEARER_RE.sub("Bearer <REDACTED>", s)
    s = _AUTHZ_RE.sub(r"\1<REDACTED>", s)
    return s


def _dig(obj: Any, *path: str) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _format_description(desc: Any, *, max_chars: int = 8000) -> tuple[str, bool, str]:
    desc_type = type(desc).__name__
    if desc is None:
        return ("", False, desc_type)
    if isinstance(desc, str):
        text = desc
    else:
        try:
            text = json.dumps(desc, ensure_ascii=False, indent=2)
        except Exception:
            text = str(desc)

    text = _redact_high_risk_secrets(text).rstrip() + ("\n" if text and not text.endswith("\n") else "")
    if len(text) <= max_chars:
        return (text, False, desc_type)
    return (text[:max_chars].rstrip() + "\n... (description truncated)\n", True, desc_type)


def _print_issue_fields(issue_path: Path) -> None:
    try:
        issue = json.loads(issue_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"(Could not parse issue JSON at {issue_path}: {e})")
        return

    fields = issue.get("fields") if isinstance(issue, dict) else {}
    summary = _dig(fields, "summary") or ""
    status = _dig(fields, "status", "name") or ""
    desc = _dig(fields, "description")
    desc_text, _truncated, desc_type = _format_description(desc)

    print("")
    print("Jira fields (sanity check)")
    if summary:
        print(f"Summary: {summary}")
    if status:
        print(f"Status: {status}")
    print(f"Description ({desc_type}):")
    if desc_text.strip():
        print(desc_text.rstrip())
    else:
        print("_No description provided._")
    print("")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jira-cursor",
        description="Fetch a Jira issue and write a Cursor-ready context bundle.",
    )
    p.add_argument("ticket", help="Ticket key (e.g. PROJ-123) or a URL containing it.")
    p.add_argument(
        "--repo",
        "--codebase",
        dest="repo",
        default=None,
        help="Codebase folder / repo root (default: git top-level of cwd, or cwd).",
    )
    p.add_argument(
        "--logs-dir",
        "--logs-folder",
        "--logs-path",
        dest="logs_dir",
        default=None,
        help="Logs folder (or single log file) used if LOG_API_URL fetch is disabled/failed.",
    )
    p.add_argument(
        "--attach",
        action="store_true",
        help="Zip the bundle and upload it as a Jira attachment (requires REST credentials).",
    )
    p.add_argument("--no-open", action="store_true", help="Do not open Cursor after writing the bundle.")
    p.add_argument(
        "--process-logs",
        dest="process_logs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Process logs (clean + summarize). Default: true.",
    )
    p.add_argument(
        "--cursor-analysis",
        dest="cursor_analysis",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Run Cursor agent analysis automatically after writing the context bundle. "
            "Requires CURSOR_API_KEY and `npm install` in the repo root. Default: false."
        ),
    )
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    # region agent log (no secrets)
    try:
        import os

        debug_log(
            run_id="pre-fix",
            hypothesis_id="H30",
            location="jira_triage/cli.py:main",
            message="CLI entry (runtime context, redacted)",
            data={
                "cwd": os.getcwd(),
                "py_executable": sys.executable,
                "module_file": __file__,
                "sys_path0": sys.path[0] if sys.path else None,
                "ticket_arg": args.ticket,
                "repo_arg": args.repo,
                "logs_dir_arg": args.logs_dir,
                "no_open": bool(args.no_open),
                "attach": bool(args.attach),
                "process_logs": bool(args.process_logs),
                "cursor_analysis": bool(args.cursor_analysis),
                "json": bool(args.json),
            },
        )
    except Exception:
        pass
    # endregion

    try:
        result = triage(
            args.ticket,
            mode="manual",
            open_cursor=not args.no_open,
            repo=args.repo,
            logs_dir=args.logs_dir,
            attach=bool(args.attach),
            process_logs=args.process_logs,
            cursor_analysis=bool(args.cursor_analysis),
        )
    except TriageError as e:
        print(str(e), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(f"Ticket: {result.ticket_key}")
    print(f"Repo: {result.repo_root}")
    print(f"Output: {result.output_dir}")
    print(f"Cursor context: {result.context_path}")
    print(f"Bundle context: {result.bundle_context_path}")
    if result.logs_path:
        print(f"Logs: {result.logs_path}")
    if result.bundle_zip_path:
        print(f"Bundle zip: {result.bundle_zip_path}")
    if result.cursor_analysis_txt_path:
        print(f"Cursor analysis: {result.cursor_analysis_txt_path}")

    _print_issue_fields(result.issue_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

