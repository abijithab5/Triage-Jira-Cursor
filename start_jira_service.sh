#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_show_help() {
  cat <<EOF
Jira Triage Service Launcher

USAGE:
  $0 [webhook|polling|help]

OPTIONS:
  webhook    Start webhook server (requires Jira admin to configure webhooks)
  polling    Start continuous polling service (works with regular Jira user)
  help       Show this help message

EXAMPLES:
  $0 webhook     # Start webhook server on port 8080
  $0 polling     # Start continuous polling every 5 minutes

POLLING vs WEBHOOK:

  Webhook Mode:
    ✓ Instant processing when tickets are created/updated
    ✓ No continuous background process
    ✗ Requires Jira administrator access to configure webhooks
    ✗ Requires public URL (ngrok, etc.) or same network as Jira

  Polling Mode:  
    ✓ No Jira admin required - works with regular user permissions
    ✓ Configurable poll intervals and custom JQL queries
    ✓ No need for public URLs or network configuration
    ✗ Slight delay based on polling interval (default 5 minutes)

Both modes produce identical analysis output and file structure.

QUICK START:
  1. Copy .env.example to .env and configure your Jira credentials
  2. Run: $0 polling    # Recommended for most users
  
For more details, see:
  - POLLING_SETUP.md (polling configuration)
  - WEBHOOK_DEBUGGING.md (webhook configuration)

EOF
}

case "${1:-help}" in
  "webhook"|"webhooks")
    echo "Starting Jira Triage Webhook Server..."
    echo "This requires Jira admin access to configure webhooks."
    echo ""
    exec "${SCRIPT_DIR}/jira_triage/setup_and_run_webhook.sh"
    ;;
    
  "poll"|"polling")
    echo "Starting Jira Triage Polling Service..." 
    echo "This works with regular Jira user permissions."
    echo ""
    exec "${SCRIPT_DIR}/jira_triage/setup_and_run_polling.sh"
    ;;
    
  "help"|"-h"|"--help")
    _show_help
    exit 0
    ;;
    
  *)
    echo "Error: Unknown option '${1}'" >&2
    echo ""
    _show_help
    exit 1
    ;;
esac