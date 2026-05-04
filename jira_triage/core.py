from __future__ import annotations

import json
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .config import ConfigError, load_config
from .context_builder import build_context_markdown, build_cursor_ticket_markdown
from .cursor_open import open_in_cursor
from .jira_attachments import upload_issue_attachment
from .jira_client import JiraError, fetch_issue, preflight_myself
from .jira_mcp import fetch_issue_via_mcp
from .logs_client import fetch_logs
from .logs_local import collect_local_logs
from .logs_processing import process_logs_file
from .repo import RepoError, extract_keywords, resolve_repo_root, suggest_repo_paths


TriageMode = Literal["manual", "webhook"]

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
    logs_path: Path | None = None
    logs_cleaned_path: Path | None = None
    logs_summary_json_path: Path | None = None
    logs_summary_path: Path | None = None
    analysis_path: Path | None = None
    repo_paths_path: Path | None = None
    bundle_zip_path: Path | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # asdict() converts Paths to strings? (it doesn't). Normalize for JSON.
        d["repo_root"] = str(self.repo_root)
        d["output_dir"] = str(self.output_dir)
        d["issue_path"] = str(self.issue_path)
        d["context_path"] = str(self.context_path)
        d["bundle_context_path"] = str(self.bundle_context_path)
        d["logs_path"] = str(self.logs_path) if self.logs_path else None
        d["logs_cleaned_path"] = str(self.logs_cleaned_path) if self.logs_cleaned_path else None
        d["logs_summary_json_path"] = str(self.logs_summary_json_path) if self.logs_summary_json_path else None
        d["logs_summary_path"] = str(self.logs_summary_path) if self.logs_summary_path else None
        d["analysis_path"] = str(self.analysis_path) if self.analysis_path else None
        d["repo_paths_path"] = str(self.repo_paths_path) if self.repo_paths_path else None
        d["bundle_zip_path"] = str(self.bundle_zip_path) if self.bundle_zip_path else None
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
) -> TriageResult:
    ticket_key = normalize_ticket_key(ticket_id_or_url)

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
    ticket_dir.mkdir(parents=True, exist_ok=True)

    # 1) Jira fetch (MCP primary; REST fallback)
    issue: dict
    jira_source_used: str | None = None
    jira_errors: dict[str, str] = {}

    if cfg.jira_source in {"auto", "mcp"}:
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
        # REST API path (fallback or forced)
        try:
            preflight = preflight_myself(cfg)
            preflight_path = ticket_dir / "jira_preflight.json"
            preflight_path.write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            myself = preflight.get("myself") if isinstance(preflight, dict) else None
            myself_ok = isinstance(myself, dict) and myself.get("ok") is True and myself.get("status_code") == 200
            if not myself_ok:
                status = (myself or {}).get("status_code") if isinstance(myself, dict) else None
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

                raise TriageError(f"{hint}{extra_s} See `{preflight_path}` for details.")

            issue = fetch_issue(cfg, ticket_key)
            jira_source_used = "api"
        except JiraError as e:
            jira_errors["api"] = str(e)
            (ticket_dir / "jira_api.error.txt").write_text(str(e) + "\n", encoding="utf-8")
            extra = ""
            if jira_errors.get("mcp"):
                extra = f" (MCP error was: {jira_errors['mcp']})"
            raise TriageError(f"Jira fetch failed via REST API: {e}{extra}") from e

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

    if logs_path is None and logs_dir_path is not None:
        local_res = collect_local_logs(ticket_dir=ticket_dir, logs_dir=logs_dir_path, ticket_key=ticket_key)
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

    # 3) Clean + structure logs (best-effort)
    logs_cleaned_path: Path | None = None
    logs_summary_json_path: Path | None = None
    logs_summary_path: Path | None = None
    logs_summary: dict | None = None
    if logs_path is not None:
        try:
            artifacts = process_logs_file(ticket_dir=ticket_dir, raw_path=logs_path)
            logs_cleaned_path = artifacts.cleaned_path
            logs_summary_json_path = artifacts.summary_json_path
            logs_summary_path = artifacts.summary_md_path
            logs_summary = artifacts.summary
        except Exception as e:
            (ticket_dir / "logs.process.error.txt").write_text(str(e) + "\n", encoding="utf-8")

    # 4) Analysis output file (for Cursor to fill)
    analysis_path = ticket_dir / "analysis.md"
    if not analysis_path.exists():
        analysis_path.write_text(_analysis_template(ticket_key, cfg.jira_base_url), encoding="utf-8")

    # 5) Select repo paths (heuristic)
    fields = issue.get("fields") if isinstance(issue, dict) else {}
    issue_summary = fields.get("summary") if isinstance(fields, dict) else ""
    issue_desc = fields.get("description") if isinstance(fields, dict) else ""
    desc_text = issue_desc if isinstance(issue_desc, str) else json.dumps(issue_desc, ensure_ascii=False)[:20_000]

    extra_kw_texts: list[str] = []
    if isinstance(logs_summary, dict):
        for e in logs_summary.get("top_exception_types") or []:
            if isinstance(e, dict) and isinstance(e.get("name"), str):
                extra_kw_texts.append(e["name"])
        for h in logs_summary.get("http_calls") or []:
            if isinstance(h, dict) and isinstance(h.get("target"), str):
                extra_kw_texts.append(h["target"])
        for h in logs_summary.get("stack_hints") or []:
            if isinstance(h, dict):
                for k in ("file", "symbol"):
                    v = h.get(k)
                    if isinstance(v, str):
                        extra_kw_texts.append(v)

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

    # 6) Cursor context (repo/.cursor/context/TICKET.md)
    cursor_context_dir = (repo_root / ".cursor" / "context").resolve()
    cursor_context_dir.mkdir(parents=True, exist_ok=True)
    cursor_context_path = cursor_context_dir / "TICKET.md"
    cursor_context_md = build_cursor_ticket_markdown(
        repo_root=repo_root,
        ticket_key=ticket_key,
        jira_base_url=cfg.jira_base_url,
        issue=issue,
        ticket_dir=ticket_dir,
        issue_path=issue_path,
        jira_source_used=jira_source_used,
        logs_path=logs_path,
        logs_cleaned_path=logs_cleaned_path,
        logs_summary_json_path=logs_summary_json_path,
        logs_summary_path=logs_summary_path,
        logs_error=logs_error,
        analysis_path=analysis_path,
        suggested_paths=suggested_paths_payload,
    )
    cursor_context_path.write_text(cursor_context_md, encoding="utf-8")

    # 7) Bundle context (out/<KEY>/context.md)
    bundle_context_path = ticket_dir / "context.md"
    context_md = build_context_markdown(
        ticket_key=ticket_key,
        jira_base_url=cfg.jira_base_url,
        issue=issue,
        issue_path=issue_path,
        logs_path=logs_path,
        logs_cleaned_path=logs_cleaned_path,
        logs_summary_json_path=logs_summary_json_path,
        logs_summary_path=logs_summary_path,
        logs_error=logs_error,
        repo_root=repo_root,
        cursor_context_path=cursor_context_path,
        analysis_path=analysis_path,
        suggested_paths=suggested_paths_payload,
        jira_source_used=jira_source_used,
    )
    bundle_context_path.write_text(context_md, encoding="utf-8")

    # 8) Optional: zip + attach to Jira
    bundle_zip_path: Path | None = None
    if attach:
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
            upload = upload_issue_attachment(cfg, ticket_key, bundle_zip_path)
        except JiraError as e:
            raise TriageError(str(e)) from e
        (ticket_dir / "jira_attachment_upload.json").write_text(
            json.dumps(
                {
                    "ok": upload.ok,
                    "status_code": upload.status_code,
                    "error": upload.error,
                    "response": upload.response_json,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if not upload.ok:
            raise TriageError(upload.error or "Attachment upload failed")

    # 9) Cursor open (manual by default; webhook gated by WEBHOOK_ALLOW_OPEN)
    should_open = False
    if open_cursor:
        if mode == "manual":
            should_open = True
        elif mode == "webhook" and cfg.webhook_allow_open:
            should_open = True

    if should_open:
        open_in_cursor(repo_root)

    return TriageResult(
        ticket_key=ticket_key,
        repo_root=repo_root,
        output_dir=ticket_dir,
        issue_path=issue_path,
        context_path=cursor_context_path,
        bundle_context_path=bundle_context_path,
        logs_path=logs_path,
        logs_cleaned_path=logs_cleaned_path,
        logs_summary_json_path=logs_summary_json_path,
        logs_summary_path=logs_summary_path,
        analysis_path=analysis_path,
        repo_paths_path=repo_paths_path,
        bundle_zip_path=bundle_zip_path,
    )

