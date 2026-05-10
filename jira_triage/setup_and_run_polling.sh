#!/usr/bin/env bash
set -euo pipefail

# Hardcode PAT here (leave empty to use env/.env). DO NOT COMMIT real tokens.
HARDCODED_PAT=""  # Set this only locally, never commit real tokens

# --- Monitoring and Debug Functions ---

_monitor_polling_logs() {
  local log_file="${1:-${PROJECT_ROOT}/logs/polling.log}"
  if [[ -f "${log_file}" ]]; then
    echo "Monitoring polling service activity (Ctrl+C to stop): ${log_file}"
    tail -f "${log_file}" | grep --line-buffered -E "(Poll cycle|Processing ticket|JQL search|POLLING)"
  else
    echo "Polling log file not found: ${log_file}"
    echo "Start the polling service first, or check POLLING_LOG_FILE setting."
  fi
}

_monitor_auth_logs() {
  local log_file="${1:-${PROJECT_ROOT}/logs/auth.log}"
  if [[ -f "${log_file}" ]]; then
    echo "Monitoring authentication attempts (Ctrl+C to stop): ${log_file}"
    tail -f "${log_file}" | grep --line-buffered -E "(Auth attempt|Probe GET|Preflight|AUTH)"
  else
    echo "Auth log file not found: ${log_file}"
    echo "Start the polling service first, or check AUTH_LOG_FILE setting."
  fi
}

_monitor_all_logs() {
  local auth_log="${1:-${PROJECT_ROOT}/logs/auth.log}"
  local polling_log="${2:-${PROJECT_ROOT}/logs/polling.log}"
  local debug_log="${3:-${PROJECT_ROOT}/logs/debug.log}"
  
  echo "Monitoring all polling logs (Ctrl+C to stop)..."
  echo "  Auth: ${auth_log}"
  echo "  Polling: ${polling_log}"
  echo "  Debug: ${debug_log}"
  echo ""
  
  # Use multitail if available, otherwise fall back to tail
  if command -v multitail >/dev/null 2>&1; then
    multitail -i "${auth_log}" -i "${polling_log}" -i "${debug_log}" 2>/dev/null || {
      echo "multitail failed, falling back to tail..."
      _monitor_fallback_all_logs "${auth_log}" "${polling_log}" "${debug_log}"
    }
  else
    _monitor_fallback_all_logs "${auth_log}" "${polling_log}" "${debug_log}"
  fi
}

_monitor_fallback_all_logs() {
  local auth_log="$1"
  local polling_log="$2"
  local debug_log="$3"
  
  {
    [[ -f "${auth_log}" ]] && tail -f "${auth_log}" | sed 's/^/[AUTH] /' &
    [[ -f "${polling_log}" ]] && tail -f "${polling_log}" | sed 's/^/[POLLING] /' &
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

_test_polling_dry_run() {
  echo "Testing polling configuration with dry run..."
  echo "  This will show what tickets would be processed without actually processing them"
  echo ""
  
  if command -v python3 >/dev/null 2>&1; then
    cd "${PROJECT_ROOT}"
    if [[ -d "venv" ]]; then
      echo "Activating virtual environment..."
      source venv/bin/activate
    fi
    
    echo "Running: jira-cursor poll --once --dry-run"
    echo ""
    jira-cursor poll --once --dry-run
  else
    echo "Python3 not available for dry run test"
  fi
}

_show_monitoring_help() {
  cat <<EOF

=== Jira Polling Monitoring and Debug Commands ===

After starting the polling service, you can monitor it using:

  # Monitor polling service activity
  ${0} monitor-polling

  # Monitor authentication attempts  
  ${0} monitor-auth

  # Monitor all logs simultaneously
  ${0} monitor-all

  # Test Jira connectivity without authentication
  ${0} test-connectivity

  # Test polling configuration with dry run
  ${0} test-polling

  # Show log file locations
  ${0} show-logs

Examples:
  # Start polling service in background and monitor activity
  ${0} &
  sleep 5
  ${0} monitor-polling

  # Test connectivity and polling before starting service
  ${0} test-connectivity
  ${0} test-polling

EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- Handle monitoring commands early ---
case "${1:-}" in
  "monitor-polling")
    cd "${PROJECT_ROOT}"
    _monitor_polling_logs "${2:-}"
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
  "test-polling")
    cd "${PROJECT_ROOT}"
    # Load .env if available
    if [[ -f "${PROJECT_ROOT}/.env" ]]; then
      set -a
      source "${PROJECT_ROOT}/.env"
      set +a
    fi
    _test_polling_dry_run
    exit 0
    ;;
  "show-logs")
    echo "Log file locations:"
    echo "  Auth: ${PROJECT_ROOT}/logs/auth.log (or \$AUTH_LOG_FILE)"  
    echo "  Polling: ${PROJECT_ROOT}/logs/polling.log (or \$POLLING_LOG_FILE)"
    echo "  Debug: ${PROJECT_ROOT}/logs/debug.log (or \$DEBUG_LOG_PATH)"
    echo "  App: ${PROJECT_ROOT}/logs/app.log (or \$APP_LOG_FILE)"
    exit 0
    ;;
  "help"|"-h"|"--help")
    _show_monitoring_help
    exit 0
    ;;
esac

# --- Setup default directories ---
DB_ROOT="${PROJECT_ROOT}/jira_triage/Triage-cursor-DB"
DEFAULT_REPO_DIR="${DB_ROOT}/repo"
DEFAULT_LOGS_DIR="${DB_ROOT}/logs"
DEFAULT_OUT_DIR="${DB_ROOT}/out"
POLLING_LOGS_DIR="${PROJECT_ROOT}/logs"

# Create default directories
mkdir -p "${DEFAULT_REPO_DIR}" "${DEFAULT_LOGS_DIR}" "${DEFAULT_OUT_DIR}"

# Create and verify logs directory for polling logging
mkdir -p "${POLLING_LOGS_DIR}"

# Test log directory writability
if ! touch "${POLLING_LOGS_DIR}/.test_write" 2>/dev/null; then
  echo "ERROR: Cannot write to logs directory: ${POLLING_LOGS_DIR}" >&2
  echo "Check directory permissions and disk space." >&2
  exit 2
fi
rm -f "${POLLING_LOGS_DIR}/.test_write"

# --- Set default environment variables ---
# These will be used if not already set in environment or .env file

# Core Jira settings
export JIRA_BASE_URL="${JIRA_BASE_URL:-https://jira.telekom.de}"
export JIRA_AUTH_MODE="${JIRA_AUTH_MODE:-bearer}"
export JIRA_SOURCE="${JIRA_SOURCE:-auto}"

# Polling configuration defaults
export JIRA_POLLING_ENABLED="${JIRA_POLLING_ENABLED:-true}"
export JIRA_POLLING_INTERVAL="${JIRA_POLLING_INTERVAL:-300}"
export JIRA_POLLING_JQL="${JIRA_POLLING_JQL:-assignee = currentUser() ORDER BY updated DESC}"
export JIRA_POLLING_MAX_RESULTS="${JIRA_POLLING_MAX_RESULTS:-50}"

# Logging defaults
export POLLING_LOG_LEVEL="${POLLING_LOG_LEVEL:-INFO}"
export POLLING_LOG_FILE="${POLLING_LOG_FILE:-${POLLING_LOGS_DIR}/polling.log}"
export AUTH_LOG_LEVEL="${AUTH_LOG_LEVEL:-INFO}"
export AUTH_LOG_FILE="${AUTH_LOG_FILE:-${POLLING_LOGS_DIR}/auth.log}"
export APP_LOG_LEVEL="${APP_LOG_LEVEL:-INFO}"
export APP_LOG_FILE="${APP_LOG_FILE:-${POLLING_LOGS_DIR}/app.log}"
export DEBUG_LOG_PATH="${DEBUG_LOG_PATH:-${POLLING_LOGS_DIR}/debug.log}"
export DEBUG_SESSION_ID="${DEBUG_SESSION_ID:-polling-session}"

# Default paths
export REPO_ROOT="${REPO_ROOT:-${DEFAULT_REPO_DIR}}"
export OUTPUT_DIR="${OUTPUT_DIR:-${DEFAULT_OUT_DIR}}"
export LOGS_DIR="${LOGS_DIR:-${DEFAULT_LOGS_DIR}}"

# --- Load .env automatically (same as Python's python-dotenv) ---
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.env"
  set +a
fi

echo ""
echo "=== Jira Polling Service Setup ==="
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

# --- Polling settings ---
polling_interval="${JIRA_POLLING_INTERVAL:-300}"
polling_jql="${JIRA_POLLING_JQL:-assignee = currentUser() ORDER BY updated DESC}"
polling_max_results="${JIRA_POLLING_MAX_RESULTS:-50}"

echo ""
printf "  %-26s %s\n" "JIRA_POLLING_INTERVAL" "${polling_interval} seconds ($(( polling_interval / 60 )) minutes)"
printf "  %-26s %s\n" "JIRA_POLLING_MAX_RESULTS" "${polling_max_results}"
echo "  JIRA_POLLING_JQL: ${polling_jql}"

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
export JIRA_POLLING_INTERVAL="${polling_interval}"
export JIRA_POLLING_JQL="${polling_jql}"
export JIRA_POLLING_MAX_RESULTS="${polling_max_results}"

if [[ -n "${logs_dir:-}" ]]; then
  export LOGS_DIR="${logs_dir}"
fi

# --- Startup summary ---
echo "=== Jira Polling Service starting ==="
echo ""
echo "  JQL Query:          ${polling_jql}"
echo "  Poll Interval:      ${polling_interval} seconds ($(( polling_interval / 60 )) minutes)"
echo "  Max Results:        ${polling_max_results} tickets per poll"
echo ""
echo "  Cursor analysis:    $([ -n "${CURSOR_API_KEY:-}" ] && echo "enabled (CURSOR_API_KEY set)" || echo "DISABLED (CURSOR_API_KEY not set)")"
echo "  Output dir:         ${output_dir}"
echo ""
echo "  Log files:"
echo "    Auth attempts:      ${PROJECT_ROOT}/logs/auth.log"
echo "    Polling activity:   ${PROJECT_ROOT}/logs/polling.log"  
echo "    Debug info:         ${PROJECT_ROOT}/logs/debug.log"
echo ""
echo "  Analysis files will appear at:"
echo "    ${output_dir}/<TICKET-KEY>/cursor_analysis.txt"
echo ""
echo "  Real-time monitoring (run in another terminal):"
echo "    ${0} monitor-polling    # Monitor polling activity"
echo "    ${0} monitor-auth       # Monitor authentication"
echo "    ${0} monitor-all        # Monitor all logs"
echo "    ${0} test-connectivity  # Test Jira connectivity"
echo "    ${0} test-polling       # Test polling configuration"
echo ""
echo "Press Ctrl+C to stop the polling service."
echo ""

# --- Clean shutdown message ---
_on_exit() {
  echo ""
  echo "Polling service stopped."
}
trap _on_exit EXIT

# --- Check if virtual environment exists ---
if [[ -d "${PROJECT_ROOT}/venv" ]]; then
  echo "Activating virtual environment..."
  source "${PROJECT_ROOT}/venv/bin/activate"
fi

# --- Start polling service ---
echo "Starting continuous polling service..."
echo "Command: jira-cursor poll --interval ${polling_interval}"
echo ""

# Use exec to replace shell so Ctrl+C goes directly to the polling service
exec jira-cursor poll --interval "${polling_interval}"