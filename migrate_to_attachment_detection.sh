#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

_show_help() {
  cat <<EOF
Migrate Existing Jira Triage Data to Attachment-Based Detection

This script migrates your existing ticket output directories to the new 
attachment-based duplicate detection system. After running this migration,
both webhook and polling modes will use Jira attachments to track processed
tickets instead of relying on local directories.

USAGE:
  $0 [options]

OPTIONS:
  --dry-run          Show what would be done without making changes
  --output-dir PATH  Specify output directory path (default: from config)
  --force            Process all tickets even if already in database
  --help, -h         Show this help message

EXAMPLES:
  # Preview what would be migrated (recommended first step)
  $0 --dry-run
  
  # Perform the actual migration
  $0
  
  # Migrate from specific output directory
  $0 --output-dir /path/to/different/out/directory
  
  # Force re-process all tickets
  $0 --force

BEFORE RUNNING:
  1. Ensure you have processed tickets in output directories
  2. Make sure your .env file is configured correctly
  3. Run with --dry-run first to preview changes

AFTER RUNNING:
  The system will use attachment-based duplicate detection:
  - Webhook mode: Checks Jira for existing analysis bundles before processing
  - Polling mode: Same attachment checking + uploads analysis bundles
  - CLI mode: Always processes (manual override)

EOF
}

case "${1:-}" in
  "help"|"-h"|"--help")
    _show_help
    exit 0
    ;;
esac

echo "=== Jira Triage Migration to Attachment-Based Detection ==="
echo ""
echo "This will migrate your existing processed tickets to use the new"
echo "attachment-based duplicate detection system."
echo ""

# Check if virtual environment exists and activate it
if [[ -d "${PROJECT_ROOT}/venv" ]]; then
  echo "Activating virtual environment..."
  source "${PROJECT_ROOT}/venv/bin/activate"
fi

# Change to project directory
cd "${PROJECT_ROOT}"

# Check if required files exist
if [[ ! -f "jira_triage/migrate_existing_data.py" ]]; then
  echo "Error: Migration script not found. Make sure you're in the correct directory." >&2
  exit 1
fi

if [[ ! -f ".env" && ! -f ".env.example" ]]; then
  echo "Warning: No .env file found. Make sure your environment is configured." >&2
fi

echo "Running migration..."
echo "Command: python -m jira_triage.migrate_existing_data $*"
echo ""

# Run the migration script with all passed arguments
python -m jira_triage.migrate_existing_data "$@"

echo ""
echo "Migration complete!"
echo ""
echo "NEXT STEPS:"
echo "1. Test the new system with: ./start_jira_service.sh polling"
echo "2. Monitor logs to ensure duplicate detection is working"
echo "3. Both webhook and polling will now check Jira attachments for duplicates"
echo ""
echo "For help with the new system, see:"
echo "  - POLLING_SETUP.md (polling configuration)"
echo "  - WEBHOOK_DEBUGGING.md (webhook configuration)"