#!/usr/bin/env bash
set -euo pipefail

# Hardcode PAT here (leave empty to use env/.env). DO NOT COMMIT real tokens.
HARDCODED_PAT=""  # Set this only locally, never commit real tokens

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DB_ROOT="${PROJECT_ROOT}/jira_triage/Triage-cursor-DB"
DEFAULT_REPO_DIR="${DB_ROOT}/repo"
DEFAULT_LOGS_DIR="${DB_ROOT}/logs"
DEFAULT_OUT_DIR="${DB_ROOT}/out"

mkdir -p "${DEFAULT_REPO_DIR}" "${DEFAULT_LOGS_DIR}" "${DEFAULT_OUT_DIR}"

# --- Load .env automatically (same as Python's python-dotenv) ---
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.env"
  set +a
fi

echo ""
echo "=== Jira Triage Webhook Setup ==="
echo ""

# Helper: show status of each env var check
_check_env() {
  local name="$1" value="$2" source="$3"
  if [[ -n "${value}" ]]; then
    printf "  %-26s [OK] using %s\n" "${name}" "${source}"
  else
    printf "  %-26s [MISSING]\n" "${name}"
  fi
}

_warn_env() {
  local name="$1" msg="$2"
  printf "  %-26s [WARNING] %s\n" "${name}" "${msg}"
}

echo "Checking environment variables..."
echo ""

# --- JIRA_BASE_URL ---
if [[ -n "${JIRA_BASE_URL:-}" ]]; then
  jira_base_url="${JIRA_BASE_URL}"
  _check_env "JIRA_BASE_URL" "${jira_base_url}" "env"
else
  jira_base_url="https://jira.telekom.de"
  _check_env "JIRA_BASE_URL" "" ""
  echo "           -> using default: ${jira_base_url}"
fi

# --- JIRA_PAT / JIRA_TOKEN (hardcoded > env; no prompt — server can't block) ---
pat_source=""
pat=""
if [[ -n "${HARDCODED_PAT:-}" ]]; then
  pat="${HARDCODED_PAT}"
  pat_source="hardcoded"
  _check_env "JIRA_PAT" "${pat}" "hardcoded"
elif [[ -n "${JIRA_PAT:-}" ]]; then
  pat="${JIRA_PAT}"
  pat_source="env:JIRA_PAT"
  _check_env "JIRA_PAT" "${pat}" "env"
elif [[ -n "${JIRA_TOKEN:-}" ]]; then
  pat="${JIRA_TOKEN}"
  pat_source="env:JIRA_TOKEN"
  _check_env "JIRA_TOKEN" "${pat}" "env"
else
  _check_env "JIRA_PAT" "" ""
  echo "           -> not set; Jira REST API will fail (add JIRA_PAT to .env)" >&2
  echo ""
  echo "ERROR: JIRA_PAT or JIRA_TOKEN is required. Add it to .env or export it." >&2
  exit 2
fi

# --- JIRA_AUTH_MODE ---
if [[ -n "${JIRA_AUTH_MODE:-}" ]]; then
  jira_auth_mode="${JIRA_AUTH_MODE}"
  _check_env "JIRA_AUTH_MODE" "${jira_auth_mode}" "env"
else
  jira_auth_mode="bearer"
  _check_env "JIRA_AUTH_MODE" "" ""
  echo "           -> using default: ${jira_auth_mode}"
fi

# --- JIRA_SOURCE ---
if [[ -n "${JIRA_SOURCE:-}" ]]; then
  jira_source="${JIRA_SOURCE}"
  _check_env "JIRA_SOURCE" "${jira_source}" "env"
else
  jira_source="auto"
  _check_env "JIRA_SOURCE" "" ""
  echo "           -> using default: ${jira_source}"
fi

# --- REPO_ROOT (required — Cursor needs this to read code) ---
if [[ -n "${REPO_ROOT:-}" ]]; then
  repo_root="${REPO_ROOT}"
  _check_env "REPO_ROOT" "${repo_root}" "env"
else
  repo_root="${DEFAULT_REPO_DIR}"
  _check_env "REPO_ROOT" "" ""
  echo "           -> using default: ${repo_root}"
fi
if [[ ! -d "${repo_root}" ]]; then
  echo ""
  echo "ERROR: Repo root does not exist or is not a directory: ${repo_root}" >&2
  echo "Tip: put your codebase into ${DEFAULT_REPO_DIR} (or set REPO_ROOT in .env)." >&2
  exit 2
fi

# --- LOGS_DIR (optional but strongly recommended for analysis quality) ---
if [[ -n "${LOGS_DIR:-}" ]]; then
  logs_dir="${LOGS_DIR}"
  _check_env "LOGS_DIR" "${logs_dir}" "env"
elif [[ -n "${LOG_API_URL:-}" ]]; then
  logs_dir=""
  _check_env "LOG_API_URL" "${LOG_API_URL}" "env (logs fetched from API)"
else
  logs_dir="${DEFAULT_LOGS_DIR}"
  _check_env "LOGS_DIR" "" ""
  echo "           -> using default: ${logs_dir}"
  _warn_env "LOGS_DIR" "no logs source configured; Cursor analysis will have no log context"
fi
if [[ -n "${logs_dir:-}" ]]; then
  mkdir -p "${logs_dir}"
fi

# --- OUTPUT_DIR ---
if [[ -n "${OUTPUT_DIR:-}" ]]; then
  output_dir="${OUTPUT_DIR}"
  _check_env "OUTPUT_DIR" "${output_dir}" "env"
else
  output_dir="${DEFAULT_OUT_DIR}"
  _check_env "OUTPUT_DIR" "" ""
  echo "           -> using default: ${output_dir}"
fi
mkdir -p "${output_dir}"

# --- CURSOR_API_KEY (optional — cursor analysis skipped without it) ---
if [[ -n "${CURSOR_API_KEY:-}" ]]; then
  _check_env "CURSOR_API_KEY" "set" "env (cursor analysis enabled)"
else
  _warn_env "CURSOR_API_KEY" "not set — cursor analysis will be skipped for every ticket"
fi

# --- CURSOR_MODEL_ID (optional) ---
if [[ -n "${CURSOR_MODEL_ID:-}" ]]; then
  _check_env "CURSOR_MODEL_ID" "${CURSOR_MODEL_ID}" "env"
else
  _check_env "CURSOR_MODEL_ID" "" ""
  echo "           -> using default: composer-2"
fi

# --- Webhook server settings ---
webhook_host="${WEBHOOK_HOST:-0.0.0.0}"
webhook_port="${WEBHOOK_PORT:-8080}"
webhook_auto_attach="${WEBHOOK_AUTO_ATTACH:-false}"
webhook_allow_open="${WEBHOOK_ALLOW_OPEN:-false}"

echo ""
printf "  %-26s %s\n" "WEBHOOK_HOST" "${webhook_host}"
printf "  %-26s %s\n" "WEBHOOK_PORT" "${webhook_port}"
printf "  %-26s %s\n" "WEBHOOK_AUTO_ATTACH" "${webhook_auto_attach}"
printf "  %-26s %s\n" "WEBHOOK_ALLOW_OPEN" "${webhook_allow_open}"

echo ""

# --- Export all vars for the subprocess ---
cd "${PROJECT_ROOT}"

export JIRA_SOURCE="${jira_source}"
export JIRA_BASE_URL="${jira_base_url}"
export JIRA_AUTH_MODE="${jira_auth_mode}"
export JIRA_TOKEN="${pat}"
export JIRA_PAT="${pat}"
export REPO_ROOT="${repo_root}"
export OUTPUT_DIR="${output_dir}"
export WEBHOOK_HOST="${webhook_host}"
export WEBHOOK_PORT="${webhook_port}"
export WEBHOOK_AUTO_ATTACH="${webhook_auto_attach}"
export WEBHOOK_ALLOW_OPEN="${webhook_allow_open}"

if [[ -n "${logs_dir:-}" ]]; then
  export LOGS_DIR="${logs_dir}"
fi

# --- Startup summary ---
echo "=== Webhook server starting ==="
echo ""
echo "  Listening:          http://${webhook_host}:${webhook_port}/jira"
echo "  Register in Jira:   use your machine's external IP or tunnel URL:"
echo "                      e.g. https://<ngrok-id>.ngrok.io/jira"
echo ""
echo "  Cursor analysis:    $([ -n "${CURSOR_API_KEY:-}" ] && echo "enabled (CURSOR_API_KEY set)" || echo "DISABLED (CURSOR_API_KEY not set)")"
echo "  Auto-attach to Jira: ${webhook_auto_attach}"
echo "  Output dir:         ${output_dir}"
echo ""
echo "  Analysis files will appear at:"
echo "    ${output_dir}/<TICKET-KEY>/cursor_analysis.txt"
echo ""
echo "Press Ctrl+C to stop the server."
echo ""

# --- Clean shutdown message ---
_on_exit() {
  echo ""
  echo "Webhook server stopped."
}
trap _on_exit EXIT

# --- Start server (exec replaces shell so Ctrl+C goes directly to uvicorn) ---
exec jira-cursor-webhook --host "${webhook_host}" --port "${webhook_port}"
