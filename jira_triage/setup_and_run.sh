#!/usr/bin/env bash
set -euo pipefail

# Hardcode PAT here (leave empty to use env/prompt). DO NOT COMMIT real tokens.
HARDCODED_PAT=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DB_ROOT="${PROJECT_ROOT}/jira_triage/Triage-cursor-DB"
DEFAULT_REPO_DIR="${DB_ROOT}/repo"
DEFAULT_LOGS_DIR="${DB_ROOT}/logs"
DEFAULT_OUT_DIR="${DB_ROOT}/out"

mkdir -p "${DEFAULT_REPO_DIR}" "${DEFAULT_LOGS_DIR}" "${DEFAULT_OUT_DIR}"

ticket="${1:-}"
if [[ -z "${ticket}" ]]; then
  read -r -p "Jira ticket key (e.g. PROJ-123): " ticket
fi
if [[ -z "${ticket}" ]]; then
  echo "Missing ticket key." >&2
  exit 2
fi

jira_base_url="${JIRA_BASE_URL:-}"
if [[ -z "${jira_base_url}" ]]; then
  read -r -p "Jira base URL (default: https://jira.telekom.de): " jira_base_url
  jira_base_url="${jira_base_url:-https://jira.telekom.de}"
fi

pat_source="hardcoded"
pat="${HARDCODED_PAT:-${JIRA_PAT:-${JIRA_TOKEN:-}}}"
if [[ -z "${HARDCODED_PAT:-}" ]]; then
  pat_source="env"
fi
if [[ -z "${pat}" ]]; then
  pat_source="prompt"
  read -rs -p "Jira PAT (input hidden): " pat
  echo ""
fi
if [[ -z "${pat}" ]]; then
  echo "Missing PAT." >&2
  exit 2
fi

repo_root="${REPO_ROOT:-}"
if [[ -z "${repo_root}" ]]; then
  read -r -p "Codebase folder (default: ${DEFAULT_REPO_DIR}): " repo_root
  repo_root="${repo_root:-${DEFAULT_REPO_DIR}}"
fi
if [[ ! -d "${repo_root}" ]]; then
  echo "Repo root does not exist or is not a directory: ${repo_root}" >&2
  echo "Tip: put your codebase into ${DEFAULT_REPO_DIR} (or point REPO_ROOT/--repo to an existing folder)." >&2
  exit 2
fi

logs_dir="${LOGS_DIR:-}"
if [[ -z "${logs_dir}" ]]; then
  read -r -p "Logs folder (default: ${DEFAULT_LOGS_DIR}): " logs_dir
  logs_dir="${logs_dir:-${DEFAULT_LOGS_DIR}}"
fi
mkdir -p "${logs_dir}"

open_cursor="${OPEN_CURSOR:-}"
if [[ -z "${open_cursor}" ]]; then
  read -r -p "Open Cursor after bundle? (y/N): " open_cursor
  open_cursor="${open_cursor:-N}"
fi

cd "${PROJECT_ROOT}"

export JIRA_SOURCE="${JIRA_SOURCE:-auto}"
export JIRA_BASE_URL="${jira_base_url}"
export JIRA_AUTH_MODE="${JIRA_AUTH_MODE:-bearer}"
export JIRA_TOKEN="${pat}"
export JIRA_PAT="${pat}"
export REPO_ROOT="${repo_root}"
export LOGS_DIR="${logs_dir}"
export OUTPUT_DIR="${OUTPUT_DIR:-${DEFAULT_OUT_DIR}}"

# Optional debug logging (NDJSON). Enable by setting JIRA_TRIAGE_DEBUG=1.
case "${JIRA_TRIAGE_DEBUG:-}" in
  1|true|TRUE|yes|YES|on|ON)
    export JIRA_TRIAGE_DEBUG_LOG_PATH="${JIRA_TRIAGE_DEBUG_LOG_PATH:-${PROJECT_ROOT}/.cursor/jira-triage.debug.ndjson}"
    ;;
esac

cmd=(PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" python3 -m jira_triage.cli "${ticket}" --repo "${repo_root}" --logs-dir "${logs_dir}")
if [[ ! "${open_cursor}" =~ ^[Yy]$ ]]; then
  cmd+=("--no-open")
fi

echo "Running: ${cmd[*]}"
"${cmd[@]}"

echo ""
echo "Bundle output base: ${OUTPUT_DIR}"
echo "Cursor context (repo): ${repo_root}/.cursor/context/TICKET.md (and TICKET.txt)"

