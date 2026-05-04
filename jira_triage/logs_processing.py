from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LogArtifacts:
    raw_path: Path
    cleaned_path: Path
    summary_json_path: Path
    summary_md_path: Path
    summary: dict[str, Any]


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_EXC_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b")
_TRACE_ID_RE = re.compile(r"\b(?:traceId|trace_id|x-b3-traceid|x_trace_id)[:=]\\s*([a-f0-9-]{8,64})\b", re.I)
_REQ_ID_RE = re.compile(r"\b(?:requestId|request_id|x-request-id|x_request_id)[:=]\\s*([a-f0-9-]{8,64})\b", re.I)
_HTTP_RE = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\\s+([^\\s]+)")
_PY_FILE_RE = re.compile(r'File\\s+"([^"]+)"\\s*,\\s*line\\s*(\\d+)')
_JS_AT_RE = re.compile(r"\\bat\\s+[^\\s]+\\s+\\(([^)]+):(\\d+):(\\d+)\\)")
_JAVA_AT_RE = re.compile(r"\\bat\\s+([a-zA-Z0-9_$.]+)\\(([^:()]+):(\\d+)\\)")


def _redact_secrets(s: str) -> str:
    # Very small set of high-risk patterns.
    s = re.sub(r"Bearer\\s+[A-Za-z0-9._\\-]{10,}", "Bearer <REDACTED>", s, flags=re.I)
    s = re.sub(r"(Authorization\\s*:\\s*)(\\S+)", r"\\1<REDACTED>", s, flags=re.I)
    return s


def clean_logs_text(text: str, *, max_lines: int = 20_000, max_line_len: int = 2_000) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ANSI_RE.sub("", text)
    text = _redact_secrets(text)
    lines = text.split("\n")
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... (truncated to first {max_lines} lines)"]
    out: list[str] = []
    for ln in lines:
        if len(ln) > max_line_len:
            out.append(ln[:max_line_len] + "... (line truncated)")
        else:
            out.append(ln)
    return "\n".join(out).rstrip() + "\n"


def _try_parse_jsonish(text: str) -> Any | None:
    s = text.strip()
    if not s:
        return None
    if not (s.startswith("{") or s.startswith("[")):
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def summarize_logs(clean_text: str) -> dict[str, Any]:
    lines = [ln for ln in clean_text.split("\n") if ln.strip()]
    lc = len(lines)

    error_lines: list[str] = []
    for ln in lines:
        l = ln.lower()
        if "error" in l or "exception" in l or "traceback" in l or "panic" in l or "fatal" in l:
            error_lines.append(ln)
            if len(error_lines) >= 50:
                break

    exc_counts: dict[str, int] = {}
    for ln in error_lines:
        for m in _EXC_RE.finditer(ln):
            exc = m.group(1)
            exc_counts[exc] = exc_counts.get(exc, 0) + 1
    top_excs = sorted(exc_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]

    trace_ids: list[str] = []
    req_ids: list[str] = []
    for ln in lines[:5000]:
        for m in _TRACE_ID_RE.finditer(ln):
            trace_ids.append(m.group(1))
        for m in _REQ_ID_RE.finditer(ln):
            req_ids.append(m.group(1))
        if len(trace_ids) >= 10 and len(req_ids) >= 10:
            break

    http_calls: list[dict[str, str]] = []
    for ln in lines[:2000]:
        m = _HTTP_RE.search(ln)
        if m:
            http_calls.append({"method": m.group(1), "target": m.group(2)})
            if len(http_calls) >= 20:
                break

    stack_files: list[dict[str, str]] = []
    for ln in lines[:5000]:
        m = _PY_FILE_RE.search(ln)
        if m:
            stack_files.append({"kind": "python", "file": m.group(1), "line": m.group(2)})
            if len(stack_files) >= 20:
                break
        m = _JS_AT_RE.search(ln)
        if m:
            stack_files.append({"kind": "js", "file": m.group(1), "line": m.group(2)})
            if len(stack_files) >= 20:
                break
        m = _JAVA_AT_RE.search(ln)
        if m:
            stack_files.append({"kind": "java", "symbol": m.group(1), "file": m.group(2), "line": m.group(3)})
            if len(stack_files) >= 20:
                break

    return {
        "line_count": lc,
        "sample_error_lines": error_lines[:15],
        "top_exception_types": [{"name": k, "count": v} for k, v in top_excs],
        "trace_ids": list(dict.fromkeys(trace_ids))[:10],
        "request_ids": list(dict.fromkeys(req_ids))[:10],
        "http_calls": http_calls[:10],
        "stack_hints": stack_files[:10],
    }


def build_logs_summary_md(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("## Logs summary")
    lines.append("")
    lines.append(f"- Line count: {summary.get('line_count')}")

    excs = summary.get("top_exception_types") or []
    if isinstance(excs, list) and excs:
        lines.append("- Top exception types:")
        for e in excs[:5]:
            if isinstance(e, dict) and e.get("name"):
                lines.append(f"  - {e.get('name')}: {e.get('count')}")

    trace_ids = summary.get("trace_ids") or []
    if isinstance(trace_ids, list) and trace_ids:
        lines.append("- Trace IDs:")
        for tid in trace_ids[:5]:
            lines.append(f"  - {tid}")

    req_ids = summary.get("request_ids") or []
    if isinstance(req_ids, list) and req_ids:
        lines.append("- Request IDs:")
        for rid in req_ids[:5]:
            lines.append(f"  - {rid}")

    errs = summary.get("sample_error_lines") or []
    if isinstance(errs, list) and errs:
        lines.append("")
        lines.append("### Error samples")
        lines.append("")
        lines.append("```")
        for ln in errs[:10]:
            lines.append(str(ln))
        lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def process_logs_file(*, ticket_dir: Path, raw_path: Path) -> LogArtifacts:
    raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
    parsed = _try_parse_jsonish(raw_text)
    if parsed is not None:
        try:
            raw_text = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            raw_text = str(parsed)

    cleaned = clean_logs_text(raw_text)
    summary = summarize_logs(cleaned)

    cleaned_path = ticket_dir / "logs.cleaned.txt"
    cleaned_path.write_text(cleaned, encoding="utf-8")

    summary_json_path = ticket_dir / "logs.summary.json"
    summary_json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_md_path = ticket_dir / "logs.summary.md"
    summary_md_path.write_text(build_logs_summary_md(summary), encoding="utf-8")

    return LogArtifacts(
        raw_path=raw_path,
        cleaned_path=cleaned_path,
        summary_json_path=summary_json_path,
        summary_md_path=summary_md_path,
        summary=summary,
    )

