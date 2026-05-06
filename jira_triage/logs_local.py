from __future__ import annotations

import gzip
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .debug_log import debug_log


@dataclass(frozen=True)
class LocalLogsResult:
    ok: bool
    source_dir: Path | None = None
    combined_path: Path | None = None
    copied_paths: list[Path] | None = None
    error: str | None = None


def _looks_like_log_file(p: Path) -> bool:
    name = p.name.lower()
    if name.startswith("."):
        return False
    if name.endswith((".log", ".txt", ".json", ".jsonl", ".ndjson", ".out", ".err", ".gz")):
        return True
    # Rotated logs like *.txt.0 / *.log.1 etc.
    if re.search(r"\.(log|txt|json|jsonl|ndjson|out|err)\.\d+$", name):
        return True
    # allow extensionless small files
    if "." not in name:
        return True
    return False


def _pick_source_dir(root: Path, ticket_key: str) -> Path:
    if root.is_file():
        return root
    direct = root / ticket_key
    if direct.exists():
        return direct
    direct2 = root / ticket_key.lower()
    if direct2.exists():
        return direct2
    direct3 = root / ticket_key.upper()
    if direct3.exists():
        return direct3
    return root


def _walk_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    skip_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cursor", "dist", "build", "out"}
    out: list[Path] = []
    max_files_seen = 25_000
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            out.append(p)
            if len(out) >= max_files_seen:
                return out
    return out


def _read_text(p: Path, *, max_bytes: int) -> str:
    try:
        if p.name.lower().endswith(".gz"):
            with gzip.open(p, "rb") as f:
                b = f.read(max_bytes + 1)
        else:
            b = p.read_bytes()[: max_bytes + 1]
    except Exception:
        return ""
    if len(b) > max_bytes:
        b = b[:max_bytes]
    return b.decode("utf-8", errors="replace")


def collect_local_logs(
    *,
    ticket_dir: Path,
    logs_dir: Path,
    ticket_key: str,
    max_files: int = 12,
    max_bytes_per_file: int = 2_000_000,
    max_copy_bytes: int = 10_000_000,
) -> LocalLogsResult:
    """
    Collect logs from a user-provided folder (fallback when LOG_API_URL fetch fails).

    Behavior:
    - If `logs_dir/<TICKET_KEY>/` exists, prefer it.
    - Select files that look like logs, prioritizing filenames containing the ticket key.
    - Copy selected files into `out/<KEY>/logs_local/` (truncating very large files).
    - Write a combined text file `out/<KEY>/logs.local.txt` for summarization.
    """
    try:
        logs_dir = logs_dir.expanduser().resolve()
    except Exception:
        logs_dir = Path(str(logs_dir)).expanduser()

    if not logs_dir.exists():
        return LocalLogsResult(ok=False, error=f"LOGS_DIR does not exist: {logs_dir}")

    source = _pick_source_dir(logs_dir, ticket_key)
    candidates = [p for p in _walk_files(source) if p.is_file() and _looks_like_log_file(p)]
    if not candidates:
        return LocalLogsResult(ok=False, source_dir=source, error=f"No log-like files found under: {source}")

    tk = ticket_key.lower()
    tk_space = tk.replace("-", " ")
    def _priority(p: Path) -> tuple[int, int]:
        n = p.name.lower()
        full = str(p).lower()
        score = 0
        if tk in n or tk in full:
            score += 100
        elif tk_space in full:
            score += 80
        # Prefer "system state" logs for incident analysis.
        if "selfheal" in n:
            score += 90
        if "systeminfo" in n or "systeminfolog" in n:
            score += 40
        if n == "messages" or "syslog" in n:
            score += 35
        if "consolelog" in n or n == "kernel" or "system_eventlog" in n or "eventlog" in n:
            score += 25
        if "error" in n or "err" in n:
            score += 10
        if "stdout" in n or "stderr" in n:
            score += 5
        try:
            size = p.stat().st_size
        except Exception:
            size = 0
        # prefer smaller files first for copying, but keep some size for signal
        return (score, -min(size, 50_000_000))

    candidates.sort(reverse=True, key=_priority)
    selected = candidates[:max_files]

    # region agent log (no secrets)
    try:
        debug_log(
            run_id="pre-fix",
            hypothesis_id="H15",
            location="jira_triage/logs_local.py:collect_local_logs",
            message="Local logs selection summary",
            data={
                "ticket_key": ticket_key,
                "source_dir": str(source),
                "candidate_count": len(candidates),
                "selected_count": len(selected),
                "selected_names": [p.name for p in selected],
                "selected_has_selfheal": any("selfheal" in p.name.lower() for p in selected),
                "selected_has_messages": any(p.name.lower() == "messages" for p in selected),
            },
        )
    except Exception:
        pass
    # endregion

    dest_dir = (ticket_dir / "logs_local").resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    combined_path = ticket_dir / "logs.local.txt"
    combined_lines: list[str] = []
    for p in selected:
        try:
            size = p.stat().st_size
        except Exception:
            size = 0

        # Copy (or truncate-copy) into the bundle folder
        dest_name = p.name
        dest = dest_dir / dest_name
        if dest.exists():
            # avoid overwriting; suffix
            stem = dest.stem
            suffix = dest.suffix
            dest = dest_dir / f"{stem}.copy{suffix}"

        try:
            if size and size <= max_copy_bytes and not p.name.lower().endswith(".gz"):
                shutil.copy2(p, dest)
            else:
                # write a truncated text representation
                text = _read_text(p, max_bytes=max_bytes_per_file)
                dest = dest_dir / f"{p.name}.truncated.txt"
                dest.write_text(text + ("\n" if text and not text.endswith("\n") else ""), encoding="utf-8")
        except Exception:
            # best effort; still include in combined
            pass

        copied.append(dest)

        # Add to combined text file (bounded)
        text = _read_text(p, max_bytes=max_bytes_per_file)
        combined_lines.append(f"===== FILE: {p} =====")
        combined_lines.append(text)
        combined_lines.append("")

    combined_path.write_text("\n".join(combined_lines).rstrip() + "\n", encoding="utf-8")
    return LocalLogsResult(ok=True, source_dir=source, combined_path=combined_path, copied_paths=copied)

