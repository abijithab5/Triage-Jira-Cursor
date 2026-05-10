# Jira-triage (Jira → Cursor)

Automatically analyze Jira tickets with AI-powered context from your codebase and logs. This tool fetches Jira issues, gathers relevant code and logs, and generates comprehensive analysis bundles for Cursor IDE.

**Key Features:**
- 🎯 **Webhook Mode**: Instant processing when tickets are created/updated  
- 🔄 **Polling Mode**: Continuous monitoring without admin access required
- 🤖 **AI-Powered Analysis**: Deep code and log analysis using Cursor AI
- 📊 **Rich Context**: Combines Jira data, codebase, and logs into actionable reports
- 🔧 **Production Ready**: Complete logging, monitoring, and error handling

## Quick Start

### 1. **Installation**

```bash
cd /path/to/Jira-triage
python3 -m venv venv
source venv/bin/activate
pip install -e .
cp .env.example .env
```

### 2. **Configure Your Environment**

Edit `.env` with your Jira credentials:
```bash
# Required
JIRA_BASE_URL=https://your-jira-instance.com
JIRA_PAT=your_personal_access_token_here

# Optional: Enable AI analysis
CURSOR_API_KEY=your_cursor_api_key_here
```

### 3. **Choose Your Mode**

```bash
# Easy launcher - choose webhook or polling
./start_jira_service.sh

# Or directly:
./start_jira_service.sh polling    # Recommended for most users
./start_jira_service.sh webhook    # Requires Jira admin access
```

## Service Modes

### **🔄 Polling Mode** *(Recommended)*
Continuously monitors Jira for new tickets assigned to you.

- ✅ **No Jira Admin Required** - Works with regular user permissions
- ✅ **Flexible Scheduling** - Configurable poll intervals (default 5 minutes)  
- ✅ **Custom Queries** - Use any JQL to target specific tickets
- ✅ **Reliable** - No network configuration or public URLs needed

```bash
# Quick start
./start_jira_service.sh polling

# Advanced configuration
jira-cursor poll --interval 60 --jql "project = MYPROJ"
```

See [POLLING_SETUP.md](POLLING_SETUP.md) for detailed configuration.

### **🎯 Webhook Mode**
Instant processing when Jira sends webhook notifications.

- ✅ **Instant Processing** - No delays, processes tickets immediately
- ✅ **Event-Driven** - Only runs when tickets change
- ❌ **Requires Jira Admin** - Need admin access to configure webhooks
- ❌ **Network Setup** - Requires public URL (ngrok, etc.)

```bash
# Quick start  
./start_jira_service.sh webhook

# Advanced configuration
jira-cursor-webhook --host 0.0.0.0 --port 8080
```

See [WEBHOOK_DEBUGGING.md](WEBHOOK_DEBUGGING.md) for detailed configuration.

## Authentication Options

- **Bearer PAT** (recommended): `JIRA_AUTH_MODE=bearer` + `JIRA_PAT=your_token`
- **Basic Auth**: `JIRA_AUTH_MODE=basic` + `JIRA_USER=...` + `JIRA_TOKEN=...`

## Manual CLI Usage

Process individual tickets directly:

```bash
# Single ticket analysis
jira-cursor PROJ-123
jira-cursor https://jira.telekom.de/browse/PROJ-123

# Custom paths and options  
jira-cursor PROJ-123 --repo "/path/to/codebase" --logs-dir "/path/to/logs"
jira-cursor PROJ-123 --no-open  # Don't open Cursor automatically
jira-cursor PROJ-123 --attach   # Attach to existing Cursor session
```

## Generated Analysis Files

Each processed ticket creates a comprehensive analysis bundle:

```
out/<TICKET-KEY>/
├── context.md              # Complete context bundle for analysis
├── context.txt             # Text-only version  
├── cursor_analysis.txt     # AI-generated analysis (requires CURSOR_API_KEY)
├── jira_issue.json         # Raw Jira ticket data
├── logs.local.txt          # Aggregated log files (if LOGS_DIR configured)
├── logs.local.json         # Structured log data
├── logs.cleaned.txt        # Cleaned/filtered logs
└── analysis.txt            # Manual analysis space

.cursor/context/<TICKET-KEY>.md   # Cursor IDE integration file
```

The context bundles include:
- **Jira Details**: Full ticket information, description, comments, history
- **Code Context**: Relevant source files and repository structure  
- **Log Analysis**: Related application logs and error traces
- **AI Insights**: Automated analysis of issues and potential solutions

## Monitoring & Debugging

Both webhook and polling modes include comprehensive monitoring:

```bash
# Real-time log monitoring
./jira_triage/setup_and_run_polling.sh monitor-all
./jira_triage/setup_and_run_webhook.sh monitor-all

# Test connectivity and configuration
./jira_triage/setup_and_run_polling.sh test-connectivity
./jira_triage/setup_and_run_polling.sh test-polling
```

Log files are organized in `logs/`:
- `auth.log` - Authentication attempts and Jira API calls
- `polling.log` / `webhook.log` - Service activity and ticket processing
- `debug.log` - Detailed debugging information
- `app.log` - General application events

## Advanced Configuration

### Jira Data Source Options
- `JIRA_SOURCE=auto` (default): REST API with MCP fallback  
- `JIRA_SOURCE=mcp`: MCP server only
- `JIRA_SOURCE=api`: Direct REST API only

### Log Integration
- `LOGS_DIR=/path/to/logs` - Local log directory scanning
- `LOG_API_URL=https://api.example.com` - Remote log API integration
- Both sources can be combined for comprehensive log analysis

### Cursor AI Integration
- `CURSOR_API_KEY=...` - Enable AI-powered analysis
- `CURSOR_MODEL_ID=composer-2` - Specify AI model (default: composer-2)

## Webhook Integration

Send webhook payloads directly:

```bash
curl -X POST "http://localhost:8080/jira" \
  -H 'Content-Type: application/json' \
  -d '{"issue":{"key":"PROJ-123"}}'
```

Enable Cursor auto-opening (requires `WEBHOOK_ALLOW_OPEN=true`):

```bash
curl -X POST "http://localhost:8080/jira?open=true" \
  -H 'Content-Type: application/json' \
  -d '{"issue":{"key":"PROJ-123"}}'
```

The webhook returns JSON: `{ ticket_id, output_dir, cursor_context_path, bundle_context_path }`

## Documentation

- **[POLLING_SETUP.md](POLLING_SETUP.md)** - Complete polling configuration guide
- **[WEBHOOK_DEBUGGING.md](WEBHOOK_DEBUGGING.md)** - Webhook setup and troubleshooting
- **[.env.example](.env.example)** - All available configuration options

---

**Need Help?** Check the monitoring logs and documentation files above. Both polling and webhook modes produce identical analysis output with comprehensive logging for troubleshooting.

