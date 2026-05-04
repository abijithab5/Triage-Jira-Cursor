from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .core import TriageError, triage


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jira-cursor",
        description="Fetch a Jira issue and write a Cursor-ready context bundle.",
    )
    p.add_argument("ticket", help="Ticket key (e.g. PROJ-123) or a URL containing it.")
    p.add_argument("--repo", default=None, help="Repo root (default: git top-level or cwd).")
    p.add_argument(
        "--logs-dir",
        default=None,
        help="Fallback folder containing logs (used if LOG_API_URL fetch is disabled/failed).",
    )
    p.add_argument(
        "--attach",
        action="store_true",
        help="Zip the bundle and upload it as a Jira attachment (requires REST credentials).",
    )
    p.add_argument("--no-open", action="store_true", help="Do not open Cursor after writing the bundle.")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    try:
        result = triage(
            args.ticket,
            mode="manual",
            open_cursor=not args.no_open,
            repo=args.repo,
            logs_dir=args.logs_dir,
            attach=bool(args.attach),
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

