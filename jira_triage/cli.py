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
from .polling_service import create_polling_service, run_polling_once


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
    """Build the argument parser with subcommands."""
    p = argparse.ArgumentParser(
        prog="jira-cursor",
        description="Jira triage tool: process individual tickets or continuously poll for assigned tickets.",
    )
    
    # Add ticket/command argument at the top level for backward compatibility
    p.add_argument(
        "command",
        nargs="?",
        metavar="{TICKET-KEY,poll}",
        help="Ticket key (e.g. PROJ-123) or 'poll' for continuous polling.",
    )
    
    # Common arguments that apply to both modes
    common_group = p.add_argument_group("common arguments")
    common_group.add_argument(
        "--repo",
        "--codebase",
        dest="repo",
        default=None,
        help="Codebase folder / repo root (default: git top-level of cwd, or cwd).",
    )
    common_group.add_argument(
        "--logs-dir",
        "--logs-folder",
        "--logs-path",
        dest="logs_dir",
        default=None,
        help="Logs folder (or single log file) used if LOG_API_URL fetch is disabled/failed.",
    )
    common_group.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    
    # Single ticket mode arguments (when using ticket directly)
    single_group = p.add_argument_group("single ticket arguments")
    single_group.add_argument(
        "--attach",
        action="store_true",
        help="Zip the bundle and upload it as a Jira attachment (requires REST credentials).",
    )
    single_group.add_argument("--no-open", action="store_true", help="Do not open Cursor after writing the bundle.")
    single_group.add_argument(
        "--process-logs",
        dest="process_logs",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=argparse.SUPPRESS,
    )
    single_group.add_argument(
        "--cursor-analysis",
        dest="cursor_analysis",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Run Cursor agent analysis automatically after writing the context bundle. "
            "Requires CURSOR_API_KEY and `npm install` in the repo root. Default: false."
        ),
    )
    
    # Magnus API log downloading arguments
    magnus_group = p.add_argument_group("Magnus API arguments (optional log source)")
    magnus_group.add_argument(
        "--magnus-log-mac",
        dest="magnus_log_mac",
        default=None,
        help="Override MAC address for Magnus log download (default: extract from Jira description or env)",
    )
    magnus_group.add_argument(
        "--magnus-log-start-date",
        dest="magnus_log_start_date",
        default=None,
        help="Override start date for Magnus log download (ISO 8601 format, e.g., 2026-05-08)",
    )
    magnus_group.add_argument(
        "--magnus-log-end-date",
        dest="magnus_log_end_date",
        default=None,
        help="Override end date for Magnus log download (ISO 8601 format, e.g., 2026-05-08)",
    )
    magnus_group.add_argument(
        "--no-magnus-merge",
        dest="no_magnus_merge",
        action="store_true",
        help="Disable auto-merging of downloaded Magnus logs (default: false, logs are auto-merged)",
    )
    
    # Poll-specific arguments (when command is 'poll')
    poll_group = p.add_argument_group("poll mode arguments")
    poll_group.add_argument(
        "--interval",
        "-i",
        type=int,
        default=None,
        help="Polling interval in seconds (default: 300 = 5 minutes, or from JIRA_POLLING_INTERVAL env var)",
    )
    poll_group.add_argument(
        "--daemon",
        "-d",
        action="store_true",
        help="Run as background daemon (requires manual process management)",
    )
    poll_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what tickets would be processed without actually processing them",
    )
    poll_group.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll cycle and exit (useful for testing or cron jobs)",
    )
    poll_group.add_argument(
        "--jql",
        default=None,
        help="Custom JQL query to find tickets (default: 'assignee = currentUser() ORDER BY updated DESC')",
    )
    poll_group.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="Maximum number of tickets to fetch per poll (default: 50, or from JIRA_POLLING_MAX_RESULTS env var)",
    )
    
    return p


def _handle_single_ticket(args: argparse.Namespace) -> int:
    """Handle single ticket processing (original CLI behavior)."""
    if not args.ticket:
        print("Error: Ticket key is required for single ticket processing", file=sys.stderr)
        print("Usage: jira-cursor TICKET-123 [options]", file=sys.stderr)
        print("   or: jira-cursor poll [options] for continuous polling", file=sys.stderr)
        return 1
    
    # region agent log (no secrets)
    try:
        import os

        debug_log(
            run_id="pre-fix",
            hypothesis_id="H30",
            location="jira_triage/cli.py:_handle_single_ticket",
            message="CLI single ticket entry (runtime context, redacted)",
            data={
                "cwd": os.getcwd(),
                "py_executable": sys.executable,
                "module_file": __file__,
                "sys_path0": sys.path[0] if sys.path else None,
                "ticket_arg": args.ticket,
                "repo_arg": args.repo,
                "logs_dir_arg": args.logs_dir,
                "no_open": bool(getattr(args, 'no_open', False)),
                "attach": bool(getattr(args, 'attach', False)),
                "process_logs": bool(getattr(args, 'process_logs', False)),
                "cursor_analysis": bool(getattr(args, 'cursor_analysis', False)),
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
            open_cursor=not getattr(args, 'no_open', False),
            repo=args.repo,
            logs_dir=args.logs_dir,
            attach=bool(getattr(args, 'attach', False)),
            process_logs=getattr(args, 'process_logs', False),
            cursor_analysis=bool(getattr(args, 'cursor_analysis', False)),
            magnus_log_mac=getattr(args, 'magnus_log_mac', None),
            magnus_log_start_date=getattr(args, 'magnus_log_start_date', None),
            magnus_log_end_date=getattr(args, 'magnus_log_end_date', None),
            magnus_auto_merge_logs=not getattr(args, 'no_magnus_merge', False),
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


def _handle_polling(args: argparse.Namespace) -> int:
    """Handle continuous polling mode."""
    try:
        from .config import load_config
        
        # Load configuration and override with CLI args if provided
        config = load_config()
        
        # Override config with CLI arguments where provided
        if hasattr(config, '_replace'):  # dataclass with _replace method
            updates = {}
            if args.jql is not None:
                updates['polling_jql'] = args.jql
            if args.max_results is not None:
                updates['polling_max_results'] = args.max_results
            if args.interval is not None:
                updates['polling_interval_seconds'] = args.interval
            if updates:
                config = config._replace(**updates)
        
        if args.once:
            # Single poll cycle
            print("Running single poll cycle...")
            stats = run_polling_once(dry_run=args.dry_run)
            
            if args.json:
                print(json.dumps(stats, ensure_ascii=False, indent=2))
            else:
                print(f"Poll completed: {stats['tickets_found']} tickets found, "
                      f"{stats['tickets_processed']} processed, {stats['tickets_failed']} failed")
            
            return 0 if stats['tickets_failed'] == 0 else 1
            
        elif args.daemon:
            # Background daemon mode
            print("Starting polling service in daemon mode...")
            print("Note: This runs in foreground. Use process manager (systemd, supervisor, etc.) for true daemon mode.")
            
        # Continuous polling
        service = create_polling_service(config)
        interval = args.interval or getattr(config, 'polling_interval_seconds', 300)
        
        print(f"Starting continuous Jira polling...")
        if args.dry_run:
            print("DRY RUN MODE: No tickets will be actually processed")
        print(f"Poll interval: {interval} seconds ({interval // 60} minutes)")
        print("Press Ctrl+C to stop")
        print()
        
        service.start(interval_seconds=interval, dry_run=args.dry_run)
        return 0
        
    except KeyboardInterrupt:
        print("\nReceived interrupt signal, stopping polling...")
        return 0
    except Exception as e:
        print(f"Error in polling mode: {e}", file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    
    # Determine if it's a polling command or a ticket
    if args.command == "poll":
        return _handle_polling(args)
    elif args.command:
        # Treat as ticket key (backward compatibility)
        args.ticket = args.command
        return _handle_single_ticket(args)
    else:
        # No command and no ticket, show help
        print("Error: Must specify either a ticket key or use the 'poll' command", file=sys.stderr)
        print()
        print("Examples:")
        print("  jira-cursor PROJ-123                    # Process single ticket")
        print("  jira-cursor poll                        # Start continuous polling")
        print("  jira-cursor poll --once --dry-run       # Test polling without processing")
        print("  jira-cursor poll --interval 60          # Poll every minute")
        print()
        print("Use 'jira-cursor --help' for full usage information")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

