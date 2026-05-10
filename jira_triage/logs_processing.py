from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .logs_local import NO_LOCAL_LOGS_STUB_MARKER


@dataclass(frozen=True)
class LogArtifacts:
    raw_path: Path
    cleaned_path: Path
    summary_json_path: Path
    summary_md_path: Path
    summary: dict[str, Any]


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_EXC_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b")
_TRACE_ID_RE = re.compile(
    r"\b(?:traceId|trace_id|x-b3-traceid|x_trace_id)[:=]\s*([a-f0-9-]{8,64})\b", re.I
)
_REQ_ID_RE = re.compile(
    r"\b(?:requestId|request_id|x-request-id|x_request_id)[:=]\s*([a-f0-9-]{8,64})\b", re.I
)
_HTTP_RE = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\s+(\S+)")
_PY_FILE_RE = re.compile(r'File\s+"([^"]+)"\s*,\s*line\s*(\d+)')
_JS_AT_RE = re.compile(r"\bat\s+\S+\s+\(([^)]+):(\d+):(\d+)\)")
_JAVA_AT_RE = re.compile(r"\bat\s+([a-zA-Z0-9_$.]+)\(([^:()]+):(\d+)\)")

_CPU_USAGE_RE = re.compile(r"\bCPU usage is\s+(\d{1,3})\b", re.I)
_MEMTOTAL_RE = re.compile(r"\bMemTotal:\s*(\d+)\s*kB\b", re.I)
_MEMFREE_RE = re.compile(r"\bMemFree:\s*(\d+)\s*kB\b", re.I)
_MEMAVAIL_RE = re.compile(r"\bMemAvailable:\s*(\d+)\s*kB\b", re.I)
_DF_ROOT_RE = re.compile(r"^/dev/root\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)%\s+(/\S*)\s*$")
_BRLAN_OK_RE = re.compile(r"\bbrlan0 exists and it has ip\b", re.I)
_GLOBAL_IPV6_RE = re.compile(r"\bglobal ipv6 is present\b", re.I)
_BRIDGE_MODE_RE = re.compile(r"\bBRIDGE_MODE is (\d+)\b", re.I)
_FIREWALL_ENABLED_RE = re.compile(r"\bFIREWALL_ENABLED is (\d+)\b", re.I)
_PACKET_LOSS_RE = re.compile(r"\bpacket loss\b", re.I)
_OOM_RE = re.compile(r"\boom\b|out of memory|oom-killer|oom killer", re.I)
_SCRIPT_ERR_RE = re.compile(r"no such file or directory|unary operator expected", re.I)
_RDKB_PLATFORM_ERR_RE = re.compile(r"\[RDKB_PLATFORM_ERROR\]")
_RDKB_PROCESS_CRASHED_RE = re.compile(r"\bRDKB_PROCESS_CRASHED\s*:", re.I)
_PARODUS_CFG_LOST_RE = re.compile(
    r"PARODUS(?:\.ERROR)?.*?connection\s+to\s+the\s+configuration\s+server\s+is\s+lost",
    re.I,
)
_EROUTER_MISSING_RE = re.compile(r"\berouter0\b.*?Device\s+not\s+found", re.I)
_PARODUS_MAC_RE = re.compile(r"--hw-mac=([0-9A-Fa-f:]+)")
_PARODUS_MODEL_RE = re.compile(r"--hw-model=([^\s]+)")
_PARODUS_FW_RE = re.compile(r"--fw-name=([^\s]+)")
_PARODUS_SERIAL_RE = re.compile(r"--hw-serial-number=([^\s]+)")
_PARODUS_REBOOT_REASON_RE = re.compile(r"--hw-last-reboot-reason=([^\s]+)")


def _redact_secrets(s: str) -> str:
    # Very small set of high-risk patterns.
    s = re.sub(r"Bearer\s+[A-Za-z0-9._\-]{10,}", "Bearer <REDACTED>", s, flags=re.I)
    s = re.sub(r"(Authorization\s*:\s*)(\S+)", r"\g<1><REDACTED>", s, flags=re.I)
    return s


def normalize_logs_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ANSI_RE.sub("", text)
    return _redact_secrets(text)


def clean_logs_from_normalized(
    normalized: str, *, max_lines: int = 20_000, max_line_len: int = 2_000
) -> str:
    lines = normalized.split("\n")
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... (truncated to first {max_lines} lines)"]
    out: list[str] = []
    for ln in lines:
        if len(ln) > max_line_len:
            out.append(ln[:max_line_len] + "... (line truncated)")
        else:
            out.append(ln)
    return "\n".join(out).rstrip() + "\n"


def clean_logs_text(text: str, *, max_lines: int = 20_000, max_line_len: int = 2_000) -> str:
    return clean_logs_from_normalized(
        normalize_logs_text(text), max_lines=max_lines, max_line_len=max_line_len
    )


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


def _extract_gw_identity_from_parodus_line(line: str) -> dict[str, str] | None:
    """Best-effort parse of parodus CLI args embedded in captured logs."""
    if "parodus" not in line or "--hw-mac=" not in line:
        return None
    out: dict[str, str] = {}
    for key, rex in (
        ("hw_mac", _PARODUS_MAC_RE),
        ("hw_model", _PARODUS_MODEL_RE),
        ("fw_name", _PARODUS_FW_RE),
        ("hw_serial_number", _PARODUS_SERIAL_RE),
        ("last_reboot_reason", _PARODUS_REBOOT_REASON_RE),
    ):
        m = rex.search(line)
        if m:
            out[key] = m.group(1).strip()
    return out if "hw_mac" in out else None


_PATTERN_SCAN_MAX_LINES = 120_000


def summarize_logs(clean_text: str, *, pattern_source_blob: str | None = None) -> dict[str, Any]:
    raw_for_stub = pattern_source_blob if pattern_source_blob is not None else clean_text
    no_local_logs_stub = NO_LOCAL_LOGS_STUB_MARKER in raw_for_stub

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

    # Device-health style signals (best-effort, bounded scan)
    cpu_vals: list[int] = []
    mem_total_kb: int | None = None
    mem_free_kb: int | None = None
    mem_available_kb: int | None = None
    root_fs: dict[str, Any] | None = None
    brlan0_has_ip = False
    global_ipv6_present = False
    bridge_mode: int | None = None
    firewall_enabled: int | None = None
    script_err_samples: list[str] = []
    packet_loss_samples: list[str] = []
    oom_samples: list[str] = []
    gw_identity_hint: dict[str, str] | None = None

    for ln in lines[:8000]:
        if gw_identity_hint is None:
            hint = _extract_gw_identity_from_parodus_line(ln)
            if hint is not None:
                gw_identity_hint = hint

        m = _CPU_USAGE_RE.search(ln)
        if m:
            try:
                v = int(m.group(1))
                if 0 <= v <= 100:
                    cpu_vals.append(v)
            except Exception:
                pass

        if mem_total_kb is None:
            m = _MEMTOTAL_RE.search(ln)
            if m:
                try:
                    mem_total_kb = int(m.group(1))
                except Exception:
                    pass
        if mem_free_kb is None:
            m = _MEMFREE_RE.search(ln)
            if m:
                try:
                    mem_free_kb = int(m.group(1))
                except Exception:
                    pass
        if mem_available_kb is None:
            m = _MEMAVAIL_RE.search(ln)
            if m:
                try:
                    mem_available_kb = int(m.group(1))
                except Exception:
                    pass

        if root_fs is None:
            m = _DF_ROOT_RE.match(ln.strip())
            if m:
                try:
                    root_fs = {
                        "device": "/dev/root",
                        "size": m.group(1),
                        "used": m.group(2),
                        "available": m.group(3),
                        "use_percent": int(m.group(4)),
                        "mount": m.group(5),
                    }
                except Exception:
                    root_fs = {"device": "/dev/root", "line": ln.strip()}

        if not brlan0_has_ip and _BRLAN_OK_RE.search(ln):
            brlan0_has_ip = True
        if not global_ipv6_present and _GLOBAL_IPV6_RE.search(ln):
            global_ipv6_present = True

        m = _BRIDGE_MODE_RE.search(ln)
        if m:
            try:
                bridge_mode = int(m.group(1))
            except Exception:
                pass

        m = _FIREWALL_ENABLED_RE.search(ln)
        if m:
            try:
                firewall_enabled = int(m.group(1))
            except Exception:
                pass

        if len(script_err_samples) < 8 and _SCRIPT_ERR_RE.search(ln):
            script_err_samples.append(ln.strip())

        if len(packet_loss_samples) < 5 and _PACKET_LOSS_RE.search(ln):
            packet_loss_samples.append(ln.strip())

        if len(oom_samples) < 5 and _OOM_RE.search(ln):
            oom_samples.append(ln.strip())

        if (
            root_fs is not None
            and mem_total_kb is not None
            and mem_available_kb is not None
            and (len(cpu_vals) >= 3 or brlan0_has_ip or global_ipv6_present)
            and len(script_err_samples) >= 2
        ):
            # Enough signals for a meaningful summary; don't over-scan.
            break

    signals: dict[str, Any] = {}
    if cpu_vals:
        avg = sum(cpu_vals) / max(len(cpu_vals), 1)
        signals["cpu_usage_percent"] = {
            "min": min(cpu_vals),
            "max": max(cpu_vals),
            "avg": round(avg, 1),
            "samples": len(cpu_vals),
        }
    if mem_total_kb is not None or mem_free_kb is not None or mem_available_kb is not None:
        mem: dict[str, Any] = {}
        if mem_total_kb is not None:
            mem["mem_total_kb"] = mem_total_kb
        if mem_free_kb is not None:
            mem["mem_free_kb"] = mem_free_kb
        if mem_available_kb is not None:
            mem["mem_available_kb"] = mem_available_kb
        if mem_total_kb and mem_available_kb is not None:
            try:
                mem["mem_available_percent"] = round((mem_available_kb / mem_total_kb) * 100.0, 1)
            except Exception:
                pass
        signals["memory"] = mem
    if root_fs is not None:
        signals["root_filesystem"] = root_fs
    if script_err_samples:
        signals["selfheal_script_issues"] = {"count": len(script_err_samples), "samples": script_err_samples[:5]}
    if brlan0_has_ip or global_ipv6_present or bridge_mode is not None or firewall_enabled is not None:
        signals["network_health"] = {
            "brlan0_has_ip": brlan0_has_ip,
            "global_ipv6_present": global_ipv6_present,
            "bridge_mode": bridge_mode,
            "firewall_enabled": firewall_enabled,
        }
    if packet_loss_samples:
        signals["packet_loss_mentions"] = {"count": len(packet_loss_samples), "samples": packet_loss_samples[:3]}
    if oom_samples:
        signals["oom_mentions"] = {"count": len(oom_samples), "samples": oom_samples[:3]}
    if gw_identity_hint:
        signals["gw_identity_hint"] = gw_identity_hint

    if no_local_logs_stub:
        signals["no_ingested_device_logs"] = {
            "stub": True,
            "note": "Local LOGS_DIR had no ingestible log files (empty folder, only dotfiles, or unsupported names).",
        }

    # Failures often appear beyond the bounded health scan; count over full blob.
    rdkb_platform_err_lines: list[str] = []
    rdkb_platform_err_count = 0
    proc_crashed_lines: list[str] = []
    proc_crashed_count = 0
    parodus_cfg_lost_lines: list[str] = []
    parodus_cfg_lost_count = 0
    erouter_missing_lines: list[str] = []
    erouter_missing_count = 0
    sample_cap = 5
    seen_parodus: set[str] = set()
    if pattern_source_blob is not None:
        pattern_lines = pattern_source_blob.split("\n")
        if len(pattern_lines) > _PATTERN_SCAN_MAX_LINES:
            pattern_lines = pattern_lines[:_PATTERN_SCAN_MAX_LINES]
    else:
        pattern_lines = lines

    for ln in pattern_lines:
        if _RDKB_PLATFORM_ERR_RE.search(ln):
            rdkb_platform_err_count += 1
            if len(rdkb_platform_err_lines) < sample_cap:
                rdkb_platform_err_lines.append(ln.strip())
        if _RDKB_PROCESS_CRASHED_RE.search(ln):
            proc_crashed_count += 1
            if len(proc_crashed_lines) < sample_cap:
                proc_crashed_lines.append(ln.strip())
        if _PARODUS_CFG_LOST_RE.search(ln):
            parodus_cfg_lost_count += 1
            s = ln.strip()
            if s not in seen_parodus and len(parodus_cfg_lost_lines) < sample_cap:
                seen_parodus.add(s)
                parodus_cfg_lost_lines.append(s)
        if _EROUTER_MISSING_RE.search(ln):
            erouter_missing_count += 1
            if len(erouter_missing_lines) < sample_cap:
                erouter_missing_lines.append(ln.strip())

    if rdkb_platform_err_count:
        signals["rdkb_platform_errors"] = {
            "count": rdkb_platform_err_count,
            "samples": rdkb_platform_err_lines,
        }
    if proc_crashed_count:
        signals["rdkb_process_crashed_events"] = {
            "count": proc_crashed_count,
            "samples": proc_crashed_lines,
        }
    if parodus_cfg_lost_count:
        signals["parodus_config_server_disconnects"] = {
            "count": parodus_cfg_lost_count,
            "samples": parodus_cfg_lost_lines,
        }
    if erouter_missing_count:
        signals["wan_erouter_missing"] = {
            "count": erouter_missing_count,
            "samples": erouter_missing_lines,
        }

    ingestion_status: str = "no_local_logs_stub" if no_local_logs_stub else "normal"

    return {
        "line_count": lc,
        "ingestion_status": ingestion_status,
        "sample_error_lines": error_lines[:15],
        "top_exception_types": [{"name": k, "count": v} for k, v in top_excs],
        "trace_ids": list(dict.fromkeys(trace_ids))[:10],
        "request_ids": list(dict.fromkeys(req_ids))[:10],
        "http_calls": http_calls[:10],
        "stack_hints": stack_files[:10],
        "signals": signals,
    }


def build_logs_summary_md(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("## Logs summary")
    lines.append("")
    lines.append(f"- Line count: {summary.get('line_count')}")
    if summary.get("ingestion_status") == "no_local_logs_stub":
        lines.append(
            "- Ingestion: no device/router log files ingested (LOGS_DIR placeholder only; "
            "see `logs.cleaned.txt` / `NO_LOCAL_LOGS_PLACEHOLDER.txt`)."
        )

    signals = summary.get("signals") or {}
    if isinstance(signals, dict) and signals:
        lines.append("- Key signals (best-effort):")

        nil = signals.get("no_ingested_device_logs")
        if isinstance(nil, dict) and nil.get("stub"):
            note = nil.get("note")
            if isinstance(note, str) and note.strip():
                lines.append(f"  - {note.strip()}")
            else:
                lines.append("  - No ingested device/router logs (local LOGS_DIR stub).")

        cpu = signals.get("cpu_usage_percent")
        if isinstance(cpu, dict) and cpu.get("min") is not None and cpu.get("max") is not None:
            lines.append(f"  - CPU usage: {cpu.get('min')}–{cpu.get('max')}% (avg {cpu.get('avg')}%, samples={cpu.get('samples')})")

        mem = signals.get("memory")
        if isinstance(mem, dict) and (mem.get("mem_total_kb") is not None or mem.get("mem_available_kb") is not None):
            mt = mem.get("mem_total_kb")
            ma = mem.get("mem_available_kb")
            mp = mem.get("mem_available_percent")
            if mt is not None and ma is not None and mp is not None:
                lines.append(f"  - Memory: MemAvailable {ma} kB / MemTotal {mt} kB (~{mp}%)")
            elif mt is not None and ma is not None:
                lines.append(f"  - Memory: MemAvailable {ma} kB / MemTotal {mt} kB")

        root = signals.get("root_filesystem")
        if isinstance(root, dict) and root.get("use_percent") is not None:
            lines.append(
                f"  - Root filesystem: {root.get('use_percent')}% used "
                f"(size={root.get('size')}, used={root.get('used')}, avail={root.get('available')}, mount={root.get('mount')})"
            )

        net = signals.get("network_health")
        if isinstance(net, dict):
            br = net.get("brlan0_has_ip")
            ip6 = net.get("global_ipv6_present")
            bm = net.get("bridge_mode")
            fw = net.get("firewall_enabled")
            parts: list[str] = []
            if br is True:
                parts.append("brlan0_has_ip")
            if ip6 is True:
                parts.append("global_ipv6_present")
            if bm is not None:
                parts.append(f"bridge_mode={bm}")
            if fw is not None:
                parts.append(f"firewall_enabled={fw}")
            if parts:
                lines.append(f"  - Network: {', '.join(parts)}")

        sh = signals.get("selfheal_script_issues")
        if isinstance(sh, dict) and sh.get("count"):
            lines.append(f"  - Selfheal script issues: {sh.get('count')} samples")

        gw = signals.get("gw_identity_hint")
        if isinstance(gw, dict) and gw.get("hw_mac"):
            parts = [
                f"MAC={gw.get('hw_mac')}",
                f"model={gw.get('hw_model')}" if gw.get("hw_model") else None,
                f"fw={gw.get('fw_name')}" if gw.get("fw_name") else None,
                f"last_reboot_reason={gw.get('last_reboot_reason')}" if gw.get("last_reboot_reason") else None,
            ]
            lines.append(f"  - Gateway identity (from logs / parodus): {', '.join(p for p in parts if p)}")

        rdkp = signals.get("rdkb_platform_errors")
        if isinstance(rdkp, dict) and rdkp.get("count"):
            lines.append(f"  - RDK-B platform errors: {rdkp.get('count')} line(s) (see JSON samples)")

        pcrash = signals.get("rdkb_process_crashed_events")
        if isinstance(pcrash, dict) and pcrash.get("count"):
            lines.append(
                f"  - RDK-B process crash / self-heal restarts: {pcrash.get('count')} event(s) "
                f"(e.g. telemetry or other processes marked not running — see JSON samples)"
            )

        par = signals.get("parodus_config_server_disconnects")
        if isinstance(par, dict) and par.get("count"):
            lines.append(f"  - Parodus remote-config disconnect events: {par.get('count')}")

        wanm = signals.get("wan_erouter_missing")
        if isinstance(wanm, dict) and wanm.get("count"):
            lines.append(f"  - erouter0 missing (ifconfig): {wanm.get('count')} occurrence(s)")

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

    normalized = normalize_logs_text(raw_text)
    cleaned = clean_logs_from_normalized(normalized)
    summary = summarize_logs(cleaned, pattern_source_blob=normalized)
    summary_md = build_logs_summary_md(summary)

    cleaned_path = ticket_dir / "logs.cleaned.txt"
    cleaned_path.write_text(cleaned, encoding="utf-8")

    summary_json_path = ticket_dir / "logs.summary.json"
    summary_json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_md_path = ticket_dir / "logs.summary.md"
    summary_md_path.write_text(summary_md, encoding="utf-8")

    # Human-readable plain text copy (same content, different extension)
    summary_txt_path = ticket_dir / "logs.summary.txt"
    summary_txt_path.write_text(summary_md, encoding="utf-8")

    return LogArtifacts(
        raw_path=raw_path,
        cleaned_path=cleaned_path,
        summary_json_path=summary_json_path,
        summary_md_path=summary_md_path,
        summary=summary,
    )

