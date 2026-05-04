from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .debug_log import debug_log
from .mcp import McpError, load_cursor_mcp_server


class ConfigError(RuntimeError):
    pass


def _parse_bool(raw: str) -> bool:
    v = raw.strip().lower()
    if v in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise ConfigError(f"Invalid boolean value: {raw!r}")


def _get(env: Mapping[str, str], key: str, *, required: bool = False, default: str | None = None) -> str | None:
    val = env.get(key, default)
    if required and (val is None or not str(val).strip()):
        raise ConfigError(f"Missing required environment variable: {key}")
    return val


@dataclass(frozen=True)
class Config:
    jira_base_url: str
    jira_user: str = ""
    jira_token: str = ""
    jira_auth_mode: str = "basic"  # basic|bearer
    jira_source: str = "auto"  # auto|mcp|api
    jira_mcp_server: str = "mcp-atlassian"
    cursor_mcp_config_path: Path | None = None

    repo_root: Path | None = None
    logs_dir: Path | None = None

    output_dir: Path = Path("out")
    jira_api_version: int = 2
    jira_verify_ssl: bool = True
    http_timeout_seconds: float = 30.0

    log_api_url: str | None = None
    log_api_method: str = "GET"  # GET|POST
    log_api_param_name: str = "ticket"
    log_api_verify_ssl: bool | None = None  # None -> use jira_verify_ssl
    webhook_allow_open: bool = False


def load_config(env: Mapping[str, str] | None = None) -> Config:
    if env is None:
        try:
            from dotenv import load_dotenv  # type: ignore
        except Exception:
            load_dotenv = None

        if load_dotenv is not None:
            load_dotenv(override=False)

        env = os.environ

    jira_source_raw = _get(env, "JIRA_SOURCE", default="auto") or "auto"
    jira_source = str(jira_source_raw).strip().lower()
    if jira_source not in {"auto", "mcp", "api"}:
        raise ConfigError("JIRA_SOURCE must be 'auto', 'mcp', or 'api'")

    jira_mcp_server = (_get(env, "JIRA_MCP_SERVER_NAME", default="mcp-atlassian") or "mcp-atlassian").strip()
    if not jira_mcp_server:
        raise ConfigError("JIRA_MCP_SERVER_NAME must be a non-empty string")

    cursor_mcp_config_path_raw = _get(env, "CURSOR_MCP_CONFIG_PATH", default=None)
    cursor_mcp_config_path = (
        Path(str(cursor_mcp_config_path_raw)).expanduser()
        if cursor_mcp_config_path_raw and str(cursor_mcp_config_path_raw).strip()
        else None
    )

    repo_root_raw = _get(env, "REPO_ROOT", default=None)
    repo_root = (
        Path(str(repo_root_raw)).expanduser()
        if repo_root_raw is not None and str(repo_root_raw).strip()
        else None
    )

    logs_dir_raw = _get(env, "LOGS_DIR", default=None)
    logs_dir = (
        Path(str(logs_dir_raw)).expanduser()
        if logs_dir_raw is not None and str(logs_dir_raw).strip()
        else None
    )

    cursor_mcp_env: dict[str, str] = {}
    try:
        server_cfg = load_cursor_mcp_server(jira_mcp_server, cursor_mcp_config_path=cursor_mcp_config_path)
    except McpError as e:
        raise ConfigError(str(e)) from e
    if server_cfg is not None:
        cursor_mcp_env = dict(server_cfg.env or {})

    jira_base_url_raw = _get(env, "JIRA_BASE_URL", required=False, default=None)
    jira_base_url = (str(jira_base_url_raw).strip() if jira_base_url_raw and str(jira_base_url_raw).strip() else "")
    if not jira_base_url:
        jira_base_url = (cursor_mcp_env.get("JIRA_URL") or "").strip()
    if not jira_base_url:
        raise ConfigError("Missing required environment variable: JIRA_BASE_URL (or configure JIRA_URL in ~/.cursor/mcp.json)")

    jira_user_raw = (_get(env, "JIRA_USER", default=None) or "").strip() or (cursor_mcp_env.get("JIRA_USERNAME") or "").strip()
    jira_token_raw = (_get(env, "JIRA_TOKEN", default=None) or "").strip() or (cursor_mcp_env.get("JIRA_API_TOKEN") or "").strip()
    jira_pat_raw = (_get(env, "JIRA_PAT", default=None) or "").strip() or (cursor_mcp_env.get("JIRA_PERSONAL_TOKEN") or "").strip()

    jira_auth_mode_raw = _get(env, "JIRA_AUTH_MODE", default=None)
    if jira_auth_mode_raw is None or not str(jira_auth_mode_raw).strip():
        jira_auth_mode = "bearer" if jira_pat_raw else "basic"
    else:
        jira_auth_mode = str(jira_auth_mode_raw).strip().lower()

    if jira_auth_mode not in {"basic", "bearer"}:
        raise ConfigError("JIRA_AUTH_MODE must be 'basic' or 'bearer'")

    # region agent log (no secrets)
    debug_log(
        run_id="pre-fix",
        hypothesis_id="H1",
        location="jira_triage/config.py:load_config",
        message="Resolved Jira auth inputs (redacted)",
        data={
            "jira_base_url_set": bool(str(jira_base_url).strip()),
            "jira_auth_mode_raw_set": bool(jira_auth_mode_raw and str(jira_auth_mode_raw).strip()),
            "jira_auth_mode": jira_auth_mode,
            "jira_source": jira_source,
            "cursor_mcp_config_present": bool(server_cfg is not None),
            "jira_mcp_server": jira_mcp_server,
            "has_jira_token": bool(jira_token_raw),
            "has_jira_pat": bool(jira_pat_raw),
        },
    )
    # endregion

    api_required = jira_source == "api"

    if jira_auth_mode == "bearer":
        jira_token = (jira_pat_raw or jira_token_raw).strip()
        if api_required and not jira_token:
            raise ConfigError("Missing required environment variable: JIRA_PAT (or JIRA_TOKEN) for JIRA_SOURCE=api")
        jira_user = jira_user_raw
    else:
        jira_user = jira_user_raw
        jira_token = jira_token_raw
        if api_required and not jira_user:
            raise ConfigError("Missing required environment variable: JIRA_USER for JIRA_SOURCE=api")
        if api_required and not jira_token:
            raise ConfigError("Missing required environment variable: JIRA_TOKEN for JIRA_SOURCE=api")

    output_dir_raw = _get(env, "OUTPUT_DIR", default="out") or "out"
    output_dir = Path(output_dir_raw).expanduser()

    api_version_raw = _get(env, "JIRA_API_VERSION", default="2") or "2"
    try:
        jira_api_version = int(api_version_raw)
    except ValueError as e:
        raise ConfigError(f"Invalid JIRA_API_VERSION: {api_version_raw!r}") from e
    if jira_api_version not in (2, 3):
        raise ConfigError("JIRA_API_VERSION must be 2 or 3")

    verify_ssl_raw = _get(env, "JIRA_VERIFY_SSL", default="true") or "true"
    jira_verify_ssl = _parse_bool(verify_ssl_raw)

    timeout_raw = _get(env, "HTTP_TIMEOUT_SECONDS", default="30") or "30"
    try:
        http_timeout_seconds = float(timeout_raw)
    except ValueError as e:
        raise ConfigError(f"Invalid HTTP_TIMEOUT_SECONDS: {timeout_raw!r}") from e

    log_api_url = _get(env, "LOG_API_URL", default=None)
    log_api_method = (_get(env, "LOG_API_METHOD", default="GET") or "GET").strip().upper()
    if log_api_method not in {"GET", "POST"}:
        raise ConfigError("LOG_API_METHOD must be GET or POST")

    log_api_param_name = (_get(env, "LOG_API_PARAM_NAME", default="ticket") or "ticket").strip()
    if not log_api_param_name:
        raise ConfigError("LOG_API_PARAM_NAME must be a non-empty string")

    log_api_verify_ssl: bool | None
    log_api_verify_ssl_raw = _get(env, "LOG_API_VERIFY_SSL", default=None)
    if log_api_verify_ssl_raw is None or not str(log_api_verify_ssl_raw).strip():
        log_api_verify_ssl = None
    else:
        log_api_verify_ssl = _parse_bool(log_api_verify_ssl_raw)

    webhook_allow_open_raw = _get(env, "WEBHOOK_ALLOW_OPEN", default="false") or "false"
    webhook_allow_open = _parse_bool(webhook_allow_open_raw)

    return Config(
        jira_base_url=str(jira_base_url).strip(),
        jira_user=str(jira_user).strip(),
        jira_token=str(jira_token).strip(),
        jira_auth_mode=jira_auth_mode,
        jira_source=jira_source,
        jira_mcp_server=jira_mcp_server,
        cursor_mcp_config_path=cursor_mcp_config_path,
        repo_root=repo_root,
        logs_dir=logs_dir,
        output_dir=output_dir,
        jira_api_version=jira_api_version,
        jira_verify_ssl=jira_verify_ssl,
        http_timeout_seconds=http_timeout_seconds,
        log_api_url=(str(log_api_url).strip() if log_api_url else None),
        log_api_method=log_api_method,
        log_api_param_name=log_api_param_name,
        log_api_verify_ssl=log_api_verify_ssl,
        webhook_allow_open=webhook_allow_open,
    )

