#!/usr/bin/env bash
set -euo pipefail

# Hardcode PAT here (leave empty to use env/prompt). DO NOT COMMIT real tokens.
HARDCODED_PAT=""  # Set this only locally, never commit real tokens

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DB_ROOT="${PROJECT_ROOT}/jira_triage/Triage-cursor-DB"
DEFAULT_REPO_DIR="${DB_ROOT}/repo"
DEFAULT_LOGS_DIR="${DB_ROOT}/logs"
DEFAULT_OUT_DIR="${DB_ROOT}/out"

mkdir -p "${DEFAULT_REPO_DIR}" "${DEFAULT_LOGS_DIR}" "${DEFAULT_OUT_DIR}"

echo ""
echo "=== Jira Triage Setup ==="
echo ""

# Helper: show status of each env var check
_check_env() {
  local name="$1" value="$2" source="$3"
  if [[ -n "${value}" ]]; then
    printf "  %-22s [OK] using %s\n" "${name}" "${source}"
  else
    printf "  %-22s [MISSING]\n" "${name}"
  fi
}

# --- Ticket (arg or prompt) ---
ticket="${1:-}"
if [[ -z "${ticket}" ]]; then
  read -r -p "Jira ticket key (e.g. PROJ-123): " ticket
fi
if [[ -z "${ticket}" ]]; then
  echo "Missing ticket key." >&2
  exit 2
fi

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

# --- JIRA_PAT / JIRA_TOKEN (hardcoded > env > prompt) ---
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
  read -rs -p "  Jira PAT (input hidden): " pat
  echo ""
  pat_source="prompt"
fi
if [[ -z "${pat}" ]]; then
  echo "Missing PAT." >&2
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

# --- REPO_ROOT ---
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
  echo "Tip: put your codebase into ${DEFAULT_REPO_DIR} (or set REPO_ROOT in your environment)." >&2
  exit 2
fi

# --- LOGS_DIR ---
if [[ -n "${LOGS_DIR:-}" ]]; then
  logs_dir="${LOGS_DIR}"
  _check_env "LOGS_DIR" "${logs_dir}" "env"
else
  logs_dir="${DEFAULT_LOGS_DIR}"
  _check_env "LOGS_DIR" "" ""
  echo "           -> using default: ${logs_dir}"
fi
mkdir -p "${logs_dir}"

# --- OUTPUT_DIR ---
if [[ -n "${OUTPUT_DIR:-}" ]]; then
  output_dir="${OUTPUT_DIR}"
  _check_env "OUTPUT_DIR" "${output_dir}" "env"
else
  output_dir="${DEFAULT_OUT_DIR}"
  _check_env "OUTPUT_DIR" "" ""
  echo "           -> using default: ${output_dir}"
fi

# --- CURSOR_API_KEY (optional) ---
if [[ -n "${CURSOR_API_KEY:-}" ]]; then
  _check_env "CURSOR_API_KEY" "${CURSOR_API_KEY}" "env (cursor analysis enabled)"
else
  _check_env "CURSOR_API_KEY" "" ""
  echo "           -> not set, cursor analysis will be skipped"
fi

# --- CURSOR_MODEL_ID (optional) ---
if [[ -n "${CURSOR_MODEL_ID:-}" ]]; then
  _check_env "CURSOR_MODEL_ID" "${CURSOR_MODEL_ID}" "env"
else
  _check_env "CURSOR_MODEL_ID" "" ""
  echo "           -> using default: composer-2"
fi

# --- Open Cursor (default: no) ---
open_cursor="${OPEN_CURSOR:-N}"

echo ""

cd "${PROJECT_ROOT}"

export JIRA_SOURCE="${jira_source}"
export JIRA_BASE_URL="${jira_base_url}"
export JIRA_AUTH_MODE="${jira_auth_mode}"
export JIRA_TOKEN="${pat}"
export JIRA_PAT="${pat}"
export REPO_ROOT="${repo_root}"
export LOGS_DIR="${logs_dir}"
export OUTPUT_DIR="${output_dir}"

# region agent log (no secrets)
{
  mkdir -p "${PROJECT_ROOT}/.cursor" >/dev/null 2>&1 || true
  printf '%s\n' "{\"sessionId\":\"1e0b79\",\"runId\":\"post-fix\",\"hypothesisId\":\"H1\",\"location\":\"jira_triage/setup_and_run.sh\",\"message\":\"setup script resolved env vars\",\"data\":{\"cwd\":\"$(pwd)\",\"project_root\":\"${PROJECT_ROOT}\",\"repo_root\":\"${repo_root}\",\"logs_dir\":\"${logs_dir}\",\"output_dir\":\"${output_dir}\",\"pat_source\":\"${pat_source}\",\"jira_base_url\":\"${jira_base_url}\",\"jira_source\":\"${jira_source}\",\"jira_auth_mode\":\"${jira_auth_mode}\",\"cursor_api_key_set\":\"$([ -n "${CURSOR_API_KEY:-}" ] && echo true || echo false)\",\"cursor_model_id\":\"${CURSOR_MODEL_ID:-composer-2}\"},\"timestamp\":$(python3 -c 'import time; print(int(time.time()*1000))' 2>/dev/null || echo 0)}" >> "${PROJECT_ROOT}/.cursor/debug-1e0b79.log"
} >/dev/null 2>&1 || true
# endregion

cmd=(python3 -m jira_triage.cli "${ticket}" --repo "${repo_root}" --logs-dir "${logs_dir}" --process-logs)

if [[ ! "${open_cursor}" =~ ^[Yy]$ ]]; then
  cmd+=("--no-open")
fi

# Auto-enable cursor analysis when API key is present in env
if [[ -n "${CURSOR_API_KEY:-}" ]]; then
  cmd+=("--cursor-analysis")
fi

echo "Running: ${cmd[*]}"
echo ""
"${cmd[@]}"

echo ""
echo "Bundle output base: ${output_dir}"
echo "Cursor context (repo): ${repo_root}/.cursor/context/TICKET.md (and TICKET.txt)"
