from __future__ import annotations

import json
import re
import zipfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from .config import ConfigError, load_config
from .context_builder import build_context_markdown, build_cursor_ticket_markdown
from .cursor_analysis import CursorAnalysisError, run_cursor_analysis
from .cursor_open import open_in_cursor
from .debug_log import debug_log
from .duplicate_detection import mark_processing_complete
from .jira_attachments import upload_issue_attachment, enhanced_upload_issue_attachment, generate_our_attachment_filename
from .jira_client import (
    JiraError,
    degraded_preflight_myself_allows_issue_fetch,
    fetch_issue,
    preflight_myself,
)
from .jira_mcp import fetch_issue_via_mcp
from .logs_client import fetch_logs
from .logs_local import NO_LOCAL_LOGS_STUB_MARKER, collect_local_logs, has_ingestible_local_logs
from .logs_processing import process_logs_file
from .magnus_log_client import MagnusLogClient
from .repo import RepoError, extract_keywords, resolve_repo_root, suggest_repo_paths


TriageMode = Literal["manual", "webhook", "polling"]

_TICKET_RE = re.compile(r"([A-Z][A-Z0-9]+-\d+)", flags=re.IGNORECASE)


class TriageError(RuntimeError):
    pass


def normalize_ticket_key(ticket_id_or_url: str) -> str:
    m = _TICKET_RE.search(ticket_id_or_url or "")
    if not m:
        raise TriageError(
            "Could not find a Jira ticket key in input. Expected something like PROJ-123 or a URL containing it."
        )
    return m.group(1).upper()


@dataclass(frozen=True)
class TriageResult:
    ticket_key: str
    repo_root: Path
    output_dir: Path
    issue_path: Path
    context_path: Path  # .cursor/context/TICKET.md
    bundle_context_path: Path  # out/<KEY>/context.md
    logs_dir_path: Path | None = None
    logs_path: Path | None = None
    logs_cleaned_path: Path | None = None
    logs_summary_json_path: Path | None = None
    logs_summary_path: Path | None = None
    analysis_path: Path | None = None
    repo_paths_path: Path | None = None
    bundle_zip_path: Path | None = None
    # Optional plain-text copies for sharing with other tools/agents.
    context_txt_path: Path | None = None  # .cursor/context/TICKET.txt
    bundle_context_txt_path: Path | None = None  # out/<KEY>/context.txt
    logs_summary_txt_path: Path | None = None  # out/<KEY>/logs.summary.txt
    analysis_txt_path: Path | None = None  # out/<KEY>/analysis.txt
    cursor_analysis_txt_path: Path | None = None  # out/<KEY>/cursor_analysis.txt

    def to_dict(self) -> dict:
        d = asdict(self)
        # asdict() converts Paths to strings? (it doesn't). Normalize for JSON.
        d["repo_root"] = str(self.repo_root)
        d["output_dir"] = str(self.output_dir)
        d["issue_path"] = str(self.issue_path)
        d["context_path"] = str(self.context_path)
        d["context_txt_path"] = str(self.context_txt_path) if self.context_txt_path else None
        d["bundle_context_path"] = str(self.bundle_context_path)
        d["bundle_context_txt_path"] = str(self.bundle_context_txt_path) if self.bundle_context_txt_path else None
        d["logs_dir_path"] = str(self.logs_dir_path) if self.logs_dir_path else None
        d["logs_path"] = str(self.logs_path) if self.logs_path else None
        d["logs_cleaned_path"] = str(self.logs_cleaned_path) if self.logs_cleaned_path else None
        d["logs_summary_json_path"] = str(self.logs_summary_json_path) if self.logs_summary_json_path else None
        d["logs_summary_path"] = str(self.logs_summary_path) if self.logs_summary_path else None
        d["analysis_path"] = str(self.analysis_path) if self.analysis_path else None
        d["analysis_txt_path"] = str(self.analysis_txt_path) if self.analysis_txt_path else None
        d["cursor_analysis_txt_path"] = str(self.cursor_analysis_txt_path) if self.cursor_analysis_txt_path else None
        d["repo_paths_path"] = str(self.repo_paths_path) if self.repo_paths_path else None
        d["bundle_zip_path"] = str(self.bundle_zip_path) if self.bundle_zip_path else None
        d["logs_summary_txt_path"] = str(self.logs_summary_txt_path) if self.logs_summary_txt_path else None
        return d


def _analysis_template(ticket_key: str, jira_base_url: str) -> str:
    link = f"{jira_base_url.rstrip('/')}/browse/{ticket_key}"
    return (
        f"# {ticket_key} analysis\n\n"
        f"- Jira: {link}\n\n"
        "## Summary\n\n"
        "## Evidence (logs + code pointers)\n\n"
        "## Root cause\n\n"
        "## Fix\n\n"
        "## Test plan\n\n"
        "## Risks / rollout notes\n\n"
    )


def _dig(obj: Any, *path: str) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _format_description(desc: Any, *, max_chars: int = 4000) -> tuple[str, str]:
    desc_type = type(desc).__name__
    if desc is None:
        return ("", desc_type)
    if isinstance(desc, str):
        text = desc
    else:
        try:
            text = json.dumps(desc, ensure_ascii=False, indent=2)
        except Exception:
            text = str(desc)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n... (truncated)\n"
    return (text, desc_type)


def _analysis_autodraft(
    *,
    ticket_key: str,
    jira_base_url: str,
    issue: dict,
    jira_source_used: str | None,
    logs_dir_path: Path | None,
    suggested_paths: list[dict[str, Any]] | None,
) -> str:
    debug_log(
        run_id="debug",
        hypothesis_id="H1,H3",
        location="jira_triage/core.py:_analysis_autodraft",
        message="Analysis autodraft started",
        data={
            "ticket_key": ticket_key,
            "has_logs_dir": logs_dir_path is not None,
            "has_suggested_paths": suggested_paths is not None and len(suggested_paths or []) > 0,
        },
    )
    link = f"{jira_base_url.rstrip('/')}/browse/{ticket_key}"
    fields = issue.get("fields") if isinstance(issue, dict) else {}

    summary = _dig(fields, "summary") or ""
    status = _dig(fields, "status", "name") or ""
    desc_text, desc_type = _format_description(_dig(fields, "description"))

    lines: list[str] = []
    lines.append(f"# {ticket_key} analysis")
    lines.append("")
    lines.append(f"- Jira: {link}")
    if jira_source_used:
        lines.append(f"- Jira source used: {jira_source_used}")
    if status:
        lines.append(f"- Status: {status}")
    if summary:
        lines.append(f"- Ticket summary: {summary}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("Auto-draft. Fill in the sections below with confirmed evidence and a concrete fix plan.")
    lines.append("")

    lines.append("## Evidence (logs + code pointers)")
    lines.append("")
    lines.append("- Bundle files (in this folder):")
    lines.append(f"  - Issue JSON: `issue.json`")
    
    if logs_dir_path is not None and logs_dir_path.exists():
        lines.append("  - Merged Logs (UTC timestamps converted to CET):")
        for p in sorted(logs_dir_path.iterdir()):
            if p.is_file() and not p.name.startswith(".") and p.suffix.lower() != ".json":
                lines.append(f"    - `{p.name}`")
        meta_summary = logs_dir_path / "metadata" / "merge_summary.txt"
        if meta_summary.is_file():
            lines.append("  - Merge statistics: `metadata/merge_summary.txt`")
    lines.append("")

    if suggested_paths:
        lines.append("### Suggested repo paths to inspect")
        lines.append("")
        for item in suggested_paths[:20]:
            p = item.get("path")
            if isinstance(p, str) and p.strip():
                lines.append(f"- `{p}`")
        lines.append("")

    lines.append("## Root cause")
    lines.append("")
    lines.append("_TBD (confirm with logs + code pointers)._")
    lines.append("")
    lines.append("## Fix")
    lines.append("")
    lines.append("_TBD (describe the minimal safe change + tests)._")
    lines.append("")
    lines.append("## Test plan")
    lines.append("")
    lines.append("_TBD (steps + expected results)._")
    lines.append("")
    lines.append("## Risks / rollout notes")
    lines.append("")
    lines.append("_TBD._")
    lines.append("")

    if desc_text:
        lines.append("## Ticket description (truncated)")
        lines.append("")
        if isinstance(_dig(fields, "description"), str):
            lines.append(desc_text)
        else:
            lines.append(f"(description type: {desc_type})")
            lines.append("```json")
            lines.append(desc_text)
            lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _apply_local_logs_dir_fallback(
    *,
    ticket_dir: Path,
    ticket_key: str,
    logs_dir_path: Path,
    logs_path: Path | None,
    logs_error: str | None,
) -> tuple[Path | None, str | None]:
    """Run LOGS_DIR collection; on success set ``logs_path`` to combined local file."""
    local_res = collect_local_logs(ticket_dir=ticket_dir, logs_dir=logs_dir_path, ticket_key=ticket_key)
    debug_log(
        run_id="debug",
        hypothesis_id="H4",
        location="jira_triage/core.py:triage",
        message="Local logs collection result",
        data={
            "ticket_key": ticket_key,
            "local_res_ok": local_res.ok,
            "combined_path_exists": local_res.combined_path is not None,
            "error": local_res.error,
        },
    )
    if local_res.ok and local_res.combined_path is not None:
        logs_path = local_res.combined_path
        (ticket_dir / "logs.local.json").write_text(
            json.dumps(
                {
                    "source_dir": str(local_res.source_dir) if local_res.source_dir else None,
                    "combined_path": str(local_res.combined_path),
                    "copied_paths": [str(p) for p in (local_res.copied_paths or [])],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        local_err = local_res.error or "Local logs collection failed"
        (ticket_dir / "logs.local.error.txt").write_text(local_err + "\n", encoding="utf-8")
        if logs_error:
            logs_error = f"{logs_error}; Local logs fallback failed: {local_err}"
        else:
            logs_error = f"Local logs fallback failed: {local_err}"
    return logs_path, logs_error


def _write_zip(bundle_dir: Path, *, extra_paths: list[tuple[Path, str]] | None = None) -> Path:
    zip_path = bundle_dir / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(bundle_dir.rglob("*")):
            if not p.is_file():
                continue
            if p.name == zip_path.name:
                continue
            arc = p.relative_to(bundle_dir).as_posix()
            zf.write(p, arcname=arc)
        for p, arcname in (extra_paths or []):
            if p.is_file():
                zf.write(p, arcname=arcname)
    return zip_path


def triage(
    ticket_id_or_url: str,
    *,
    mode: TriageMode = "manual",
    open_cursor: bool = True,
    repo: str | None = None,
    logs_dir: str | None = None,
    attach: bool = False,
    process_logs: bool = False,
    cursor_analysis: bool = False,
    magnus_log_mac: str | None = None,
    magnus_log_start_date: str | None = None,
    magnus_log_end_date: str | None = None,
    magnus_auto_merge_logs: bool | None = None,
) -> TriageResult:
    ticket_key = normalize_ticket_key(ticket_id_or_url)

    debug_log(
        run_id="implementation",
        hypothesis_id="H1,H2",
        location="jira_triage/core.py:triage",
        message="Triage function started",
        data={
            "ticket_key": ticket_key,
            "process_logs": process_logs,
            "mode": mode,
            "logs_dir_provided": logs_dir is not None,
        },
    )

    try:
        cfg = load_config()
    except ConfigError as e:
        raise TriageError(str(e)) from e

    # Repo root (for `.cursor/context/TICKET.md` + placing `out/` inside repo)
    try:
        repo_root = resolve_repo_root(repo or (str(cfg.repo_root) if cfg.repo_root else None))
    except RepoError as e:
        raise TriageError(str(e)) from e

    out_base = cfg.output_dir
    if not out_base.is_absolute():
        out_base = (repo_root / out_base).resolve()
    ticket_dir = (out_base / ticket_key).resolve()
    
    debug_log(
        run_id="debug",
        hypothesis_id="E",
        location="jira_triage/core.py:triage",
        message="Creating output directory",
        data={
            "ticket_key": ticket_key,
            "out_base": str(out_base),
            "ticket_dir": str(ticket_dir),
            "ticket_dir_exists_before": ticket_dir.exists(),
        },
    )

    ticket_dir.mkdir(parents=True, exist_ok=True)

    debug_log(
        run_id="debug",
        hypothesis_id="E",
        location="jira_triage/core.py:triage",
        message="Output directory creation attempted",
        data={
            "ticket_key": ticket_key,
            "ticket_dir": str(ticket_dir),
            "ticket_dir_exists_after": ticket_dir.exists(),
            "ticket_dir_is_dir": ticket_dir.is_dir() if ticket_dir.exists() else False,
        },
    )

    # 1) Jira fetch (REST primary; MCP fallback)
    issue: dict
    jira_source_used: str | None = None
    jira_errors: dict[str, str] = {}

    api_attempted = False
    mcp_attempted = False

    if cfg.jira_source in {"auto", "api"}:
        api_attempted = True
        
        debug_log(
            run_id="debug",
            hypothesis_id="B",
            location="jira_triage/core.py:triage",
            message="Starting Jira API authentication",
            data={
                "ticket_key": ticket_key,
                "jira_source": cfg.jira_source,
                "jira_base_url": cfg.jira_base_url,
                "jira_auth_mode": cfg.jira_auth_mode,
            },
        )

        try:
            api_cfgs: list[Config] = [cfg]
            api_sources: list[str] = []
            if cfg.jira_auth_mode == "bearer" and getattr(cfg, "jira_bearer_token_candidates", ()):
                tokens = list(cfg.jira_bearer_token_candidates)
                sources = list(getattr(cfg, "jira_bearer_token_candidate_sources", ()))
                api_cfgs = [replace(cfg, jira_token=t) for t in tokens]
                api_sources = sources

            api_attempts: list[dict[str, Any]] = []
            for i, cfg_try in enumerate(api_cfgs):
                preflight = preflight_myself(cfg_try)
                preflight_path = ticket_dir / ("jira_preflight.json" if i == 0 else f"jira_preflight.alt{i+1}.json")
                preflight_path.write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                myself = preflight.get("myself") if isinstance(preflight, dict) else None
                status = (myself or {}).get("status_code") if isinstance(myself, dict) else None
                api_attempts.append(
                    {
                        "idx": i,
                        "token_source": (api_sources[i] if i < len(api_sources) else None),
                        "myself_status": status,
                        "preflight_path": preflight_path.name,
                    }
                )

                myself_ok = isinstance(myself, dict) and myself.get("ok") is True and myself.get("status_code") == 200
                if myself_ok:
                    try:
                        issue = fetch_issue(cfg_try, ticket_key)
                        jira_source_used = "api"
                        cfg = cfg_try
                        break
                    except JiraError as e:
                        if "(401" in str(e) and i + 1 < len(api_cfgs):
                            continue

                        # region agent log
                        debug_log(
                            run_id="pre-fix",
                            hypothesis_id="H18",
                            location="jira_triage/core.py:triage",
                            message="Jira REST issue fetch failed after preflight (attempt summary)",
                            data={"ticket_key": ticket_key, "attempts": api_attempts, "error": str(e)},
                        )
                        # endregion
                        raise

                # Only retry on 401 when we have another bearer candidate.
                if status == 401 and i + 1 < len(api_cfgs):
                    continue

                # Degraded: /myself forbidden but session looks authenticated — try issue read anyway.
                if isinstance(myself, dict) and degraded_preflight_myself_allows_issue_fetch(myself):
                    try:
                        issue = fetch_issue(cfg_try, ticket_key)
                        jira_source_used = "api"
                        cfg = cfg_try
                        warn_path = ticket_dir / "jira_preflight.warning.txt"
                        warn_path.write_text(
                            "GET /rest/api/.../myself returned HTTP 403 (not permitted for this "
                            "account/token), but GET issue/{key} succeeded. Jira triage will proceed "
                            "using the REST API.\n",
                            encoding="utf-8",
                        )
                        break
                    except JiraError as fetch_err:
                        api_attempts[-1]["issue_fetch_after_myself_403_error"] = str(fetch_err)
                        login_reason = myself.get("x_seraph_loginreason")
                        req_id = myself.get("x_arequestid")
                        extra = []
                        if login_reason:
                            extra.append(f"loginReason={login_reason}")
                        if req_id:
                            extra.append(f"requestId={req_id}")
                        extra_s = f" ({', '.join(extra)})" if extra else ""
                        raise TriageError(
                            f"Jira REST: GET /myself returned 403{extra_s} and issue fetch also failed: {fetch_err}. "
                            f"See `{preflight_path}` for details."
                        ) from fetch_err

                login_reason = (myself or {}).get("x_seraph_loginreason") if isinstance(myself, dict) else None
                req_id = (myself or {}).get("x_arequestid") if isinstance(myself, dict) else None

                hint = "Jira preflight failed: GET /myself did not return 200."
                if status == 401:
                    hint = "Jira preflight failed (401): token not accepted for GET /myself."
                elif status == 403:
                    hint = "Jira preflight failed (403): authenticated but not authorized for GET /myself."

                extra = []
                if login_reason:
                    extra.append(f"loginReason={login_reason}")
                if req_id:
                    extra.append(f"requestId={req_id}")
                extra_s = f" ({', '.join(extra)})" if extra else ""

                # region agent log
                debug_log(
                    run_id="pre-fix",
                    hypothesis_id="H18",
                    location="jira_triage/core.py:triage",
                    message="Jira REST preflight failed (attempt summary)",
                    data={"ticket_key": ticket_key, "attempts": api_attempts},
                )
                # endregion

                raise TriageError(f"{hint}{extra_s} See `{preflight_path}` for details.")

            if jira_source_used == "api":
                # region agent log
                debug_log(
                    run_id="pre-fix",
                    hypothesis_id="H18",
                    location="jira_triage/core.py:triage",
                    message="Jira REST succeeded (attempt summary)",
                    data={"ticket_key": ticket_key, "attempts": api_attempts},
                )
                # endregion
        except (JiraError, TriageError) as e:
            debug_log(
                run_id="debug",
                hypothesis_id="B",
                location="jira_triage/core.py:triage",
                message="Jira API authentication/fetch failed",
                data={
                    "ticket_key": ticket_key,
                    "error_type": type(e).__name__,
                    "error_message": str(e)[:500],
                    "jira_source": cfg.jira_source,
                },
            )

            jira_errors["api"] = str(e)
            (ticket_dir / "jira_api.error.txt").write_text(str(e) + "\n", encoding="utf-8")
            if cfg.jira_source == "api":
                raise TriageError(f"Jira fetch failed via REST API: {e}") from e

    if jira_source_used is None and cfg.jira_source in {"auto", "mcp"}:
        mcp_attempted = True
        try:
            mcp_res = fetch_issue_via_mcp(cfg, ticket_key)
            issue = mcp_res.issue
            jira_source_used = "mcp"
            if mcp_res.server_info is not None:
                (ticket_dir / "jira_mcp_server_info.json").write_text(
                    json.dumps(mcp_res.server_info, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        except JiraError as e:
            jira_errors["mcp"] = str(e)
            (ticket_dir / "jira_mcp.error.txt").write_text(str(e) + "\n", encoding="utf-8")
            if cfg.jira_source == "mcp":
                raise TriageError(f"Jira MCP fetch failed: {e}") from e

    if jira_source_used is None:
        api_e = jira_errors.get("api")
        mcp_e = jira_errors.get("mcp")
        attempts = []
        if api_attempted:
            attempts.append("api")
        if mcp_attempted:
            attempts.append("mcp")
        attempts_s = ", ".join(attempts) if attempts else "none"
        extra = []
        if api_e:
            extra.append(f"API error: {api_e}")
        if mcp_e:
            extra.append(f"MCP error: {mcp_e}")
        extra_s = " | ".join(extra) if extra else "No error detail captured."
        raise TriageError(f"Jira fetch failed (attempted: {attempts_s}). {extra_s}")

    (ticket_dir / "jira_source.json").write_text(
        json.dumps(
            {
                "configured_source": cfg.jira_source,
                "source_used": jira_source_used,
                "errors": jira_errors,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    issue_path = ticket_dir / "issue.json"
    issue_path.write_text(json.dumps(issue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 2) Optional logs fetch (non-fatal)
    logs_path: Path | None = None
    logs_error: str | None = None
    logs_result = fetch_logs(cfg, ticket_key)
    if logs_result is not None:
        if logs_result.ok:
            if logs_result.parsed_json is not None:
                logs_path = ticket_dir / "logs.json"
                logs_path.write_text(
                    json.dumps(logs_result.parsed_json, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            else:
                logs_path = ticket_dir / "logs.txt"
                text = logs_result.text
                if text is None and logs_result.body is not None:
                    text = logs_result.body.decode("utf-8", errors="replace")
                logs_path.write_text((text or "") + ("\n" if text and not text.endswith("\n") else ""), encoding="utf-8")
        else:
            logs_error = logs_result.error or "Unknown error while fetching logs"
            (ticket_dir / "logs.error.txt").write_text(logs_error + "\n", encoding="utf-8")

    # Local logs fallback (folder provided by user)
    logs_dir_path: Path | None = None
    if logs_dir and str(logs_dir).strip():
        logs_dir_path = Path(str(logs_dir)).expanduser()
    elif cfg.logs_dir is not None:
        logs_dir_path = cfg.logs_dir

    has_local_ingest = (
        has_ingestible_local_logs(logs_dir_path, ticket_key) if logs_dir_path is not None else False
    )
    debug_log(
        run_id="debug",
        hypothesis_id="H4",
        location="jira_triage/core.py:triage",
        message="Local logs collection attempt (when ingestible files exist)",
        data={
            "ticket_key": ticket_key,
            "logs_path_is_none": logs_path is None,
            "logs_dir_path_provided": logs_dir_path is not None,
            "logs_dir_path": str(logs_dir_path) if logs_dir_path else None,
            "has_ingestible_local": has_local_ingest,
        },
    )

    if logs_path is None and logs_dir_path is not None and has_local_ingest:
        logs_path, logs_error = _apply_local_logs_dir_fallback(
            ticket_dir=ticket_dir,
            ticket_key=ticket_key,
            logs_dir_path=logs_dir_path,
            logs_path=logs_path,
            logs_error=logs_error,
        )

    # 2.5) Magnus API logs fetch (optional, non-fatal)
    magnus_stats = None
    if cfg.magnus_log_api_enabled:
        try:
            from datetime import datetime
            
            client = MagnusLogClient(cfg)
            description = issue.get("fields", {}).get("description") if isinstance(issue, dict) else None
            
            # Parse dates from CLI parameters if provided
            start_date = None
            end_date = None
            if magnus_log_start_date:
                try:
                    start_date = datetime.fromisoformat(magnus_log_start_date.replace("Z", "+00:00"))
                except Exception:
                    pass
            if magnus_log_end_date:
                try:
                    end_date = datetime.fromisoformat(magnus_log_end_date.replace("Z", "+00:00"))
                except Exception:
                    pass
            
            magnus_stats = client.download_logs(
                ticket_key=ticket_key,
                mac_address=magnus_log_mac,  # Will use CLI override, config, or extract from description
                start_date=start_date,       # Will use CLI override or extract from description
                end_date=end_date,           # Will use CLI override or extract from description
                output_dir=ticket_dir / "logs" / "magnus",
                description=description,
                issue_data=issue,
                auto_merge_logs=magnus_auto_merge_logs if magnus_auto_merge_logs is not None else getattr(cfg, "magnus_auto_merge_logs", True),
            )
            
            if not magnus_stats.success:
                print(f"⚠️  Magnus log download failed: {magnus_stats.error}")
            else:
                print(f"✅ Magnus logs downloaded successfully ({magnus_stats.logs_downloaded} files)")
                
                # Point logs_dir_path to the merged directory and skip single log picking
                if getattr(magnus_stats, "merged_dir", None) and magnus_stats.merged_dir:
                    logs_dir_path = magnus_stats.merged_dir
                    # Since we are keeping raw merged data, we don't pick a single critical log
                    logs_path = None
                    
        except Exception as e:
            print(f"❌ Error downloading Magnus logs: {e}")

    debug_log(
        run_id="debug",
        hypothesis_id="H4",
        location="jira_triage/core.py:triage",
        message="Local logs fallback after Magnus (placeholder or error)",
        data={
            "ticket_key": ticket_key,
            "logs_path_is_none": logs_path is None,
            "logs_dir_path": str(logs_dir_path) if logs_dir_path else None,
        },
    )
    if logs_path is None and logs_dir_path is not None and not cfg.magnus_log_api_enabled:
        # Only fallback to local if Magnus was not enabled or didn't set a merged_dir
        logs_path, logs_error = _apply_local_logs_dir_fallback(
            ticket_dir=ticket_dir,
            ticket_key=ticket_key,
            logs_dir_path=logs_dir_path,
            logs_path=logs_path,
            logs_error=logs_error,
        )

    if logs_path is not None and logs_path.is_file():
        try:
            _preview = logs_path.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            _preview = ""
        if NO_LOCAL_LOGS_STUB_MARKER in _preview:
            print(
                "ℹ️  LOGS_DIR had no ingestible log files (empty tree, dotfiles only, or unsupported "
                f"names). See {ticket_dir / 'logs_local' / 'NO_LOCAL_LOGS_PLACEHOLDER.txt'} — add "
                "`.log`/`.txt` under LOGS_DIR/<KEY>/ or enable Magnus."
            )

    # 3) Skip Clean + structure logs
    logs_cleaned_path: Path | None = None
    logs_summary_json_path: Path | None = None
    logs_summary_path: Path | None = None
    logs_summary_txt_path: Path | None = None
    logs_summary: dict | None = None
    
    debug_log(
        run_id="implementation",
        hypothesis_id="H2",
        location="jira_triage/core.py:triage",
        message="Skipping log processing - using raw logs only",
        data={
            "ticket_key": ticket_key,
            "reason": "Log processing removed per requirements",
        },
    )

    # 4) Select repo paths (heuristic)
    fields = issue.get("fields") if isinstance(issue, dict) else {}
    issue_summary = fields.get("summary") if isinstance(fields, dict) else ""
    issue_desc = fields.get("description") if isinstance(fields, dict) else ""
    desc_text = issue_desc if isinstance(issue_desc, str) else json.dumps(issue_desc, ensure_ascii=False)[:20_000]

    extra_kw_texts: list[str] = []

    keywords = extract_keywords(
        [
            ticket_key,
            str(issue_summary or ""),
            desc_text,
            *extra_kw_texts,
        ]
    )

    suggestions = suggest_repo_paths(repo_root, keywords=keywords, max_paths=20)
    suggested_paths_payload = [
        {"path": s.path, "score": s.score, "reasons": list(s.reasons)} for s in suggestions
    ]
    repo_paths_path = ticket_dir / "repo_paths.json"
    repo_paths_path.write_text(json.dumps(suggested_paths_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 5) Analysis output file (template or auto-draft)
    analysis_path = (ticket_dir / "analysis.md").resolve()
    analysis_txt_path = (ticket_dir / "analysis.txt").resolve()
    analysis_template = _analysis_template(ticket_key, cfg.jira_base_url)
    analysis_template_s = analysis_template.strip()

    def _safe_read(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        except Exception:
            return ""

    def _safe_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except Exception:
            return 0.0

    existing_md = _safe_read(analysis_path)
    existing_txt = _safe_read(analysis_txt_path)
    existing_md_s = existing_md.strip()
    existing_txt_s = existing_txt.strip()

    md_is_template = bool(existing_md_s) and existing_md_s == analysis_template_s
    txt_is_template = bool(existing_txt_s) and existing_txt_s == analysis_template_s

    candidates: list[tuple[str, str, float]] = []
    if existing_md_s and not md_is_template:
        candidates.append(("analysis.md", existing_md, _safe_mtime(analysis_path)))
    if existing_txt_s and not txt_is_template:
        candidates.append(("analysis.txt", existing_txt, _safe_mtime(analysis_txt_path)))

    analysis_mode = "kept_existing"
    analysis_source_used: str = "unknown"
    analysis_body: str
    if candidates:
        candidates.sort(key=lambda t: t[2], reverse=True)
        analysis_source_used, analysis_body, _ = candidates[0]
        analysis_mode = f"synced_from_{analysis_source_used}"
    else:
        # No user-authored analysis found (only template/empty). Generate a fresh auto-draft.
        analysis_source_used = "autodraft"
        analysis_mode = "wrote_autodraft"
        analysis_body = _analysis_autodraft(
            ticket_key=ticket_key,
            jira_base_url=cfg.jira_base_url,
            issue=issue,
            jira_source_used=jira_source_used,
            logs_dir_path=logs_dir_path,
            suggested_paths=suggested_paths_payload,
        )

    if analysis_body and not analysis_body.endswith("\n"):
        analysis_body += "\n"

    analysis_md_len: int | None = None
    analysis_txt_len: int | None = None
    wrote_md = False
    wrote_txt = False
    try:
        if _safe_read(analysis_path) != analysis_body:
            analysis_path.write_text(analysis_body, encoding="utf-8")
            wrote_md = True
        analysis_md_len = len(analysis_body)
    except Exception:
        pass
    try:
        if _safe_read(analysis_txt_path) != analysis_body:
            analysis_txt_path.write_text(analysis_body, encoding="utf-8")
            wrote_txt = True
        analysis_txt_len = len(analysis_body)
    except Exception:
        analysis_txt_path = None

    # region agent log
    sig = logs_summary.get("signals") if isinstance(logs_summary, dict) else None
    cpu = sig.get("cpu_usage_percent") if isinstance(sig, dict) else None
    root = sig.get("root_filesystem") if isinstance(sig, dict) else None
    sh = sig.get("selfheal_script_issues") if isinstance(sig, dict) else None
    net = sig.get("network_health") if isinstance(sig, dict) else None
    debug_log(
        run_id="pre-fix",
        hypothesis_id="H14",
        location="jira_triage/core.py:triage",
        message="Analysis file status",
        data={
            "analysis_mode": analysis_mode,
            "analysis_source_used": analysis_source_used,
            "analysis_contains_autodraft_phrase": ("Auto-draft." in analysis_body),
            "analysis_md_path": str(analysis_path),
            "analysis_md_len": analysis_md_len,
            "analysis_txt_path": str(analysis_txt_path) if analysis_txt_path else None,
            "analysis_txt_len": analysis_txt_len,
            "analysis_wrote_md": wrote_md,
            "analysis_wrote_txt": wrote_txt,
            "signals_cpu_range": (
                f"{cpu.get('min')}–{cpu.get('max')}" if isinstance(cpu, dict) and cpu.get("min") is not None and cpu.get("max") is not None else None
            ),
            "signals_root_fs_use_percent": (root.get("use_percent") if isinstance(root, dict) else None),
            "signals_selfheal_issue_count": (sh.get("count") if isinstance(sh, dict) else None),
            "signals_brlan0_has_ip": (net.get("brlan0_has_ip") if isinstance(net, dict) else None),
            "signals_global_ipv6_present": (net.get("global_ipv6_present") if isinstance(net, dict) else None),
        },
    )
    # endregion

    # 6) Cursor context (repo/.cursor/context/TICKET.md)
    cursor_context_dir = (repo_root / ".cursor" / "context").resolve()
    cursor_context_dir.mkdir(parents=True, exist_ok=True)
    cursor_context_path = cursor_context_dir / "TICKET.md"

    # Logs directory reference for context (prefer @-path within this repo when possible)
    logs_source_dir_for_context: Path | None = None
    if logs_dir_path is not None:
        logs_source_dir_for_context = logs_dir_path
    logs_source_ref: str | None = None
    if logs_source_dir_for_context is not None:
        try:
            tool_root = Path(__file__).resolve().parents[1]
            rel = logs_source_dir_for_context.resolve().relative_to(tool_root.resolve()).as_posix()
            logs_source_ref = f"@{rel}"
        except Exception:
            logs_source_ref = f"`{logs_source_dir_for_context}`"

    logs_copied_dir_name: str | None = None
    try:
        if (ticket_dir / "logs_local").is_dir():
            logs_copied_dir_name = "logs_local"
    except Exception:
        logs_copied_dir_name = None

    # region agent log
    debug_log(
        run_id="pre-fix",
        hypothesis_id="H22",
        location="jira_triage/core.py:triage",
        message="Logs source references for context",
        data={
            "ticket_key": ticket_key,
            "logs_source_dir": str(logs_source_dir_for_context) if logs_source_dir_for_context else None,
            "logs_source_ref": logs_source_ref,
            "logs_copied_dir_name": logs_copied_dir_name,
            "logs_path": str(logs_path) if logs_path else None,
        },
    )
    # endregion

    cursor_context_md = build_cursor_ticket_markdown(
        repo_root=repo_root,
        ticket_key=ticket_key,
        jira_base_url=cfg.jira_base_url,
        issue=issue,
        ticket_dir=ticket_dir,
        issue_path=issue_path,
        jira_source_used=jira_source_used,
        logs_source_ref=logs_source_ref,
        logs_copied_dir_name=logs_copied_dir_name,
        logs_path=logs_path,
        logs_cleaned_path=logs_cleaned_path,
        logs_summary_json_path=logs_summary_json_path,
        logs_summary_path=logs_summary_path,
        logs_summary_txt_path=logs_summary_txt_path,
        logs_error=logs_error,
        analysis_path=analysis_path,
        analysis_txt_path=analysis_txt_path,
        suggested_paths=suggested_paths_payload,
    )
    cursor_context_path.write_text(cursor_context_md, encoding="utf-8")
    cursor_context_txt_path: Path | None = None
    try:
        cursor_context_txt_path = (cursor_context_dir / "TICKET.txt").resolve()
        cursor_context_txt_path.write_text(cursor_context_md, encoding="utf-8")
    except Exception:
        cursor_context_txt_path = None

    # 7) Bundle context (out/<KEY>/context.md)
    bundle_context_path = ticket_dir / "context.md"
    context_md = build_context_markdown(
        ticket_key=ticket_key,
        jira_base_url=cfg.jira_base_url,
        issue=issue,
        issue_path=issue_path,
        logs_source_ref=logs_source_ref,
        logs_copied_dir_name=logs_copied_dir_name,
        logs_path=logs_path,
        logs_cleaned_path=logs_cleaned_path,
        logs_summary_json_path=logs_summary_json_path,
        logs_summary_path=logs_summary_path,
        logs_summary_txt_path=logs_summary_txt_path,
        logs_error=logs_error,
        repo_root=repo_root,
        cursor_context_path=cursor_context_path,
        analysis_path=analysis_path,
        analysis_txt_path=analysis_txt_path,
        suggested_paths=suggested_paths_payload,
        jira_source_used=jira_source_used,
    )
    bundle_context_path.write_text(context_md, encoding="utf-8")
    bundle_context_txt_path: Path | None = None
    try:
        bundle_context_txt_path = (ticket_dir / "context.txt").resolve()
        bundle_context_txt_path.write_text(context_md, encoding="utf-8")
    except Exception:
        bundle_context_txt_path = None

    # 8) Optional: Cursor auto-analysis
    cursor_analysis_txt_path: Path | None = None
    if cursor_analysis and cfg.cursor_api_key:
        try:
            cursor_analysis_out = ticket_dir / "cursor_analysis.txt"
            run_cursor_analysis(
                context_path=cursor_context_path,
                repo_root=repo_root,
                analysis_txt_path=cursor_analysis_out,
                cursor_api_key=cfg.cursor_api_key,
                model_id=cfg.cursor_model_id,
            )
            cursor_analysis_txt_path = cursor_analysis_out
        except CursorAnalysisError as e:
            (ticket_dir / "cursor_analysis.error.txt").write_text(str(e) + "\n", encoding="utf-8")

    # region agent log
    debug_log(
        run_id="pre-fix",
        hypothesis_id="H12",
        location="jira_triage/core.py:triage",
        message="Wrote plain-text copies (best-effort)",
        data={
            "context_txt_path": str(cursor_context_txt_path) if cursor_context_txt_path else None,
            "bundle_context_txt_path": str(bundle_context_txt_path) if bundle_context_txt_path else None,
            "logs_summary_txt_path": str(logs_summary_txt_path) if logs_summary_txt_path else None,
            "analysis_txt_path": str(analysis_txt_path) if analysis_txt_path else None,
        },
    )
    # endregion

    # 9) Optional: zip + attach to Jira
    # In webhook mode, attach is gated by WEBHOOK_AUTO_ATTACH env var.
    # In polling mode, always attach when attach=True.
    # In manual mode, always attach when attach=True.
    should_attach = attach and (mode == "manual" or mode == "polling" or cfg.webhook_auto_attach)
    bundle_zip_path: Path | None = None
    if should_attach:
        try:
            bundle_zip_path = _write_zip(
                ticket_dir,
                extra_paths=[
                    (cursor_context_path, ".cursor/context/TICKET.md"),
                ],
            )
        except Exception as e:
            raise TriageError(f"Failed to create bundle zip: {e}") from e

        try:
            # Use enhanced upload with consistent filename for our analysis bundles
            attachment_filename = generate_our_attachment_filename(ticket_key)
            upload = enhanced_upload_issue_attachment(cfg, ticket_key, bundle_zip_path, 
                                                   custom_filename=attachment_filename)
        except JiraError as e:
            raise TriageError(str(e)) from e
        
        # Save upload result
        upload_result = {
            "ok": upload.ok,
            "status_code": upload.status_code,
            "error": upload.error,
            "response": upload.response_json,
            "attachment_filename": attachment_filename,
        }
        
        (ticket_dir / "jira_attachment_upload.json").write_text(
            json.dumps(upload_result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        
        if not upload.ok:
            raise TriageError(upload.error or "Attachment upload failed")
        
        # Mark ticket as processed in our database after successful upload
        try:
            # Extract attachment ID from upload response
            attachment_id = None
            if upload.response_json and isinstance(upload.response_json, list) and len(upload.response_json) > 0:
                attachment_id = str(upload.response_json[0].get("id", ""))
            elif upload.response_json and isinstance(upload.response_json, dict):
                attachment_id = str(upload.response_json.get("id", ""))
            
            # Get analysis content for change detection (from analysis file if it exists)
            analysis_content = None
            analysis_md_path = ticket_dir / "analysis.md"
            if analysis_md_path.exists():
                analysis_content = analysis_md_path.read_text(encoding="utf-8", errors="ignore")
            
            mark_processing_complete(
                config=cfg,
                ticket_key=ticket_key,
                processing_mode=mode,
                jira_attachment_id=attachment_id,
                attachment_filename=attachment_filename,
                analysis_content=analysis_content
            )
            
            debug_log(
                run_id="debug",
                hypothesis_id="H1",
                location="jira_triage/core.py:triage",
                message="processing_marked_complete",
                data={
                    "ticket_key": ticket_key,
                    "mode": mode,
                    "attachment_id": attachment_id,
                    "attachment_filename": attachment_filename
                }
            )
            
        except Exception as e:
            # Log but don't fail the entire process
            debug_log(
                run_id="debug",
                hypothesis_id="H1",
                location="jira_triage/core.py:triage",
                message="processing_complete_marking_failed",
                data={
                    "ticket_key": ticket_key,
                    "error": str(e),
                    "error_type": type(e).__name__
                }
            )

    # 10) Cursor open (manual by default; webhook gated by WEBHOOK_ALLOW_OPEN)
    should_open = False
    if open_cursor:
        if mode == "manual":
            should_open = True
        elif mode == "webhook" and cfg.webhook_allow_open:
            should_open = True

    if should_open:
        open_in_cursor(repo_root)

    # region agent log (no secrets)
    try:
        cursor_context_len = len(cursor_context_md) if isinstance(cursor_context_md, str) else None
    except Exception:
        cursor_context_len = None
    debug_log(
        run_id="pre-fix",
        hypothesis_id="H9",
        location="jira_triage/core.py:triage",
        message="Triage finished; artifact summary (paths only)",
        data={
            "ticket_key": ticket_key,
            "jira_source_configured": cfg.jira_source,
            "jira_source_used": jira_source_used,
            "repo_root": str(repo_root),
            "ticket_dir": str(ticket_dir),
            "wrote_cursor_context": str(cursor_context_path),
            "cursor_context_len": cursor_context_len,
            "logs_path": str(logs_path) if logs_path is not None else None,
            "logs_cleaned_path": str(logs_cleaned_path) if logs_cleaned_path is not None else None,
            "logs_summary_path": str(logs_summary_path) if logs_summary_path is not None else None,
            "analysis_path": str(analysis_path) if analysis_path is not None else None,
            "repo_paths_path": str(repo_paths_path) if repo_paths_path is not None else None,
            "bundle_zip_path": str(bundle_zip_path) if bundle_zip_path is not None else None,
            "opened_cursor": bool(should_open),
        },
    )
    # endregion

    return TriageResult(
        ticket_key=ticket_key,
        repo_root=repo_root,
        output_dir=ticket_dir,
        issue_path=issue_path,
        context_path=cursor_context_path,
        bundle_context_path=bundle_context_path,
        logs_dir_path=logs_dir_path,
        logs_path=logs_path,
        logs_cleaned_path=logs_cleaned_path,
        logs_summary_json_path=logs_summary_json_path,
        logs_summary_path=logs_summary_path,
        analysis_path=analysis_path,
        repo_paths_path=repo_paths_path,
        bundle_zip_path=bundle_zip_path,
        context_txt_path=cursor_context_txt_path,
        bundle_context_txt_path=bundle_context_txt_path,
        logs_summary_txt_path=logs_summary_txt_path,
        analysis_txt_path=analysis_txt_path,
        cursor_analysis_txt_path=cursor_analysis_txt_path,
    )

