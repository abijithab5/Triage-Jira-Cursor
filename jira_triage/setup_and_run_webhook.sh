#!/usr/bin/env bash
set -euo pipefail

# Hardcode PAT here (leave empty to use env/.env). DO NOT COMMIT real tokens.
HARDCODED_PAT=""  # Set this only locally, never commit real tokens

# --- Monitoring and Debug Functions ---

_monitor_webhook_logs() {
  local log_file="${1:-${PROJECT_ROOT}/logs/webhook.log}"
  if [[ -f "${log_file}" ]]; then
    echo "Monitoring webhook requests (Ctrl+C to stop): ${log_file}"
    tail -f "${log_file}" | grep --line-buffered -E "(Request (started|completed|failed)|WEBHOOK)"
  else
    echo "Webhook log file not found: ${log_file}"
    echo "Start the webhook server first, or check WEBHOOK_LOG_FILE setting."
  fi
}

_monitor_auth_logs() {
  local log_file="${1:-${PROJECT_ROOT}/logs/auth.log}"
  if [[ -f "${log_file}" ]]; then
    echo "Monitoring authentication attempts (Ctrl+C to stop): ${log_file}"
    tail -f "${log_file}" | grep --line-buffered -E "(Auth attempt|Probe GET|Preflight|AUTH)"
  else
    echo "Auth log file not found: ${log_file}"
    echo "Start the webhook server first, or check AUTH_LOG_FILE setting."
  fi
}

_monitor_all_logs() {
  local webhook_log="${1:-${PROJECT_ROOT}/logs/webhook.log}"
  local auth_log="${2:-${PROJECT_ROOT}/logs/auth.log}"
  local debug_log="${3:-${PROJECT_ROOT}/logs/debug.log}"
  
  echo "Monitoring all logs (Ctrl+C to stop)..."
  echo "  Webhook: ${webhook_log}"
  echo "  Auth: ${auth_log}"
  echo "  Debug: ${debug_log}"
  echo ""
  
  # Use multitail if available, otherwise fall back to tail
  if command -v multitail >/dev/null 2>&1; then
    multitail -i "${webhook_log}" -i "${auth_log}" -i "${debug_log}" 2>/dev/null || {
      echo "multitail failed, falling back to tail..."
      _monitor_fallback_all_logs "${webhook_log}" "${auth_log}" "${debug_log}"
    }
  else
    _monitor_fallback_all_logs "${webhook_log}" "${auth_log}" "${debug_log}"
  fi
}

_monitor_fallback_all_logs() {
  local webhook_log="$1"
  local auth_log="$2"
  local debug_log="$3"
  
  {
    [[ -f "${webhook_log}" ]] && tail -f "${webhook_log}" | sed 's/^/[WEBHOOK] /' &
    [[ -f "${auth_log}" ]] && tail -f "${auth_log}" | sed 's/^/[AUTH] /' &
    [[ -f "${debug_log}" ]] && tail -f "${debug_log}" | sed 's/^/[DEBUG] /' &
    wait
  }
}

_test_jira_connectivity() {
  echo "Testing Jira connectivity..."
  echo "  Base URL: ${jira_base_url}"
  echo "  SSL verification: $([ "${config_jira_verify_ssl:-true}" = "false" ] && echo "disabled" || echo "enabled")"
  echo ""
  
  # Basic connectivity test
  if command -v curl >/dev/null 2>&1; then
    local curl_opts=()
    if [ "${config_jira_verify_ssl:-true}" = "false" ]; then
      curl_opts+=(-k)
    fi
    
    echo "Testing basic connectivity (curl)..."
    if curl "${curl_opts[@]}" -s -f -m 10 "${jira_base_url}/status" >/dev/null 2>&1; then
      echo "  ✓ Basic connectivity to ${jira_base_url} successful"
    else
      echo "  ✗ Cannot reach ${jira_base_url}"
      echo "  Check network connectivity and base URL"
    fi
    
    echo ""
    echo "Testing serverInfo endpoint..."
    local serverinfo_url="${jira_base_url}/rest/api/2/serverInfo"
    if curl "${curl_opts[@]}" -s -f -m 10 "${serverinfo_url}" >/dev/null 2>&1; then
      echo "  ✓ ServerInfo endpoint accessible"
    else
      echo "  ✗ ServerInfo endpoint failed"
      echo "  This might indicate network issues or incorrect base URL"
    fi
  else
    echo "curl not available, skipping connectivity test"
  fi
}

_show_monitoring_help() {
  cat <<EOF

=== Monitoring and Debug Commands ===

After starting the webhook server, you can monitor it using:

  # Monitor webhook requests in real-time
  ${0} monitor-webhook

  # Monitor authentication attempts  
  ${0} monitor-auth

  # Monitor all logs simultaneously
  ${0} monitor-all

  # Test Jira connectivity without authentication
  ${0} test-connectivity

  # Show log file locations
  ${0} show-logs

Examples:
  # Start server in background and monitor requests
  ${0} &
  sleep 5
  ${0} monitor-webhook

  # Test connectivity before starting server
  ${0} test-connectivity

EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- Handle monitoring commands early ---
case "${1:-}" in
  "monitor-webhook")
    cd "${PROJECT_ROOT}"
    _monitor_webhook_logs "${2:-}"
    exit 0
    ;;
  "monitor-auth") 
    cd "${PROJECT_ROOT}"
    _monitor_auth_logs "${2:-}"
    exit 0
    ;;
  "monitor-all")
    cd "${PROJECT_ROOT}"
    _monitor_all_logs "${2:-}" "${3:-}" "${4:-}"
    exit 0
    ;;
  "test-connectivity")
    # Need to load config first for this command
    if [[ -f "${PROJECT_ROOT}/.env" ]]; then
      set -a
      source "${PROJECT_ROOT}/.env"
      set +a
    fi
    jira_base_url="${JIRA_BASE_URL:-https://jira.telekom.de}"
    config_jira_verify_ssl="${JIRA_VERIFY_SSL:-true}"
    _test_jira_connectivity
    exit 0
    ;;
  "show-logs")
    echo "Log file locations:"
    echo "  Webhook: ${PROJECT_ROOT}/logs/webhook.log (or \$WEBHOOK_LOG_FILE)"
    echo "  Auth: ${PROJECT_ROOT}/logs/auth.log (or \$AUTH_LOG_FILE)"  
    echo "  Debug: ${PROJECT_ROOT}/logs/debug.log (or \$DEBUG_LOG_PATH)"
    echo "  App: ${PROJECT_ROOT}/logs/app.log (or \$APP_LOG_FILE)"
    exit 0
    ;;
  "help"|"-h"|"--help")
    _show_monitoring_help
    exit 0
    ;;
esac

DB_ROOT="${PROJECT_ROOT}/jira_triage/Triage-cursor-DB"
DEFAULT_REPO_DIR="${DB_ROOT}/repo"
DEFAULT_LOGS_DIR="${DB_ROOT}/logs"
DEFAULT_OUT_DIR="${DB_ROOT}/out"

# Create default directories
mkdir -p "${DEFAULT_REPO_DIR}" "${DEFAULT_LOGS_DIR}" "${DEFAULT_OUT_DIR}"

# Create and verify logs directory for webhook logging
WEBHOOK_LOGS_DIR="${PROJECT_ROOT}/logs"
mkdir -p "${WEBHOOK_LOGS_DIR}"

# Test log directory writability
if ! touch "${WEBHOOK_LOGS_DIR}/.test_write" 2>/dev/null; then
  echo "ERROR: Cannot write to logs directory: ${WEBHOOK_LOGS_DIR}" >&2
  echo "Check directory permissions and disk space." >&2
  exit 2
fi
rm -f "${WEBHOOK_LOGS_DIR}/.test_write"

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
echo "  Log files:"
echo "    Webhook requests:   ${PROJECT_ROOT}/logs/webhook.log"
echo "    Authentication:     ${PROJECT_ROOT}/logs/auth.log"  
echo "    Debug info:         ${PROJECT_ROOT}/logs/debug.log"
echo ""
echo "  Analysis files will appear at:"
echo "    ${output_dir}/<TICKET-KEY>/cursor_analysis.txt"
echo ""
echo "  Real-time monitoring (run in another terminal):"
echo "    ${0} monitor-webhook    # Monitor webhook requests"
echo "    ${0} monitor-auth       # Monitor authentication"
echo "    ${0} monitor-all        # Monitor all logs"
echo "    ${0} test-connectivity  # Test Jira connectivity"
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
# Use module invocation (same pattern as setup_and_run.sh which uses python3 -m jira_triage.cli)
# Falls back to installed entry point if available.
if command -v jira-cursor-webhook >/dev/null 2>&1; then
  exec jira-cursor-webhook --host "${webhook_host}" --port "${webhook_port}"
else
  exec python3 -m jira_triage.webhook --host "${webhook_host}" --port "${webhook_port}"
fi
