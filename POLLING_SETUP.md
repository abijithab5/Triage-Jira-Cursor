# Jira Polling Service Setup Guide

This guide shows you how to set up and run the continuous Jira polling service as an alternative to webhooks.

## Quick Start

### 1. **Setup Your Environment**

Copy the example environment file and configure it:
```bash
cp .env.example .env
```

Edit `.env` and set your Jira credentials:
```bash
# Required: Jira Authentication
JIRA_BASE_URL=https://your-jira-instance.com
JIRA_PAT=your_personal_access_token_here

# Optional: Customize polling behavior
JIRA_POLLING_INTERVAL=300  # Poll every 5 minutes
JIRA_POLLING_JQL=assignee = currentUser() ORDER BY updated DESC
JIRA_POLLING_MAX_RESULTS=50

# Optional: Enable Cursor AI analysis
CURSOR_API_KEY=your_cursor_api_key_here
```

### 2. **Start the Polling Service**

```bash
# Activate virtual environment
source venv/bin/activate

# Start the polling service
./jira_triage/setup_and_run_polling.sh
```

## Service Management

### **Test Before Running**

```bash
# Test Jira connectivity
./jira_triage/setup_and_run_polling.sh test-connectivity

# Test polling configuration (dry run)
./jira_triage/setup_and_run_polling.sh test-polling
```

### **Monitoring**

In separate terminals, monitor the service:

```bash
# Monitor polling activity
./jira_triage/setup_and_run_polling.sh monitor-polling

# Monitor authentication attempts
./jira_triage/setup_and_run_polling.sh monitor-auth

# Monitor all logs
./jira_triage/setup_and_run_polling.sh monitor-all

# Show log file locations
./jira_triage/setup_and_run_polling.sh show-logs
```

### **Alternative Start Methods**

```bash
# Manual control with custom options
source venv/bin/activate
jira-cursor poll --interval 60                    # Poll every minute
jira-cursor poll --once --dry-run                 # Test single poll
jira-cursor poll --jql "project = MYPROJ"         # Custom JQL
```

## Configuration Options

### **Environment Variables**

| Variable | Default | Description |
|----------|---------|-------------|
| `JIRA_BASE_URL` | https://jira.telekom.de | Your Jira instance URL |
| `JIRA_PAT` | *(required)* | Personal Access Token |
| `JIRA_POLLING_INTERVAL` | 300 | Seconds between polls (300 = 5 minutes) |
| `JIRA_POLLING_JQL` | assignee = currentUser() ORDER BY updated DESC | JQL query for tickets |
| `JIRA_POLLING_MAX_RESULTS` | 50 | Maximum tickets per poll |
| `CURSOR_API_KEY` | *(optional)* | Enable AI-powered analysis |

### **Default Directories**

- **Logs**: `logs/` (polling.log, auth.log, debug.log)
- **Output**: `jira_triage/Triage-cursor-DB/out/<TICKET-KEY>/`
- **Repository**: `jira_triage/Triage-cursor-DB/repo/`

### **JQL Query Examples**

```bash
# Only your assigned tickets
JIRA_POLLING_JQL="assignee = currentUser() ORDER BY updated DESC"

# Specific project
JIRA_POLLING_JQL="assignee = currentUser() AND project = MYPROJ ORDER BY updated DESC"

# Exclude closed tickets
JIRA_POLLING_JQL="assignee = currentUser() AND status != Closed ORDER BY updated DESC"

# Recent tickets only
JIRA_POLLING_JQL="assignee = currentUser() AND updated >= -7d ORDER BY updated DESC"
```

## Generated Output

For each ticket found, the service creates identical output to webhook mode:

```
out/<TICKET-KEY>/
├── context.md              # Full context bundle
├── context.txt             # Text version
├── cursor_analysis.txt     # AI analysis (if CURSOR_API_KEY set)
├── jira_issue.json         # Raw Jira data
└── analysis.txt            # Manual analysis file

.cursor/context/<TICKET-KEY>.md  # Cursor IDE integration
```

## Troubleshooting

### **No Tickets Found**

```bash
# Check your JQL query
./jira_triage/setup_and_run_polling.sh test-polling

# Monitor auth logs for issues
./jira_triage/setup_and_run_polling.sh monitor-auth
```

### **Authentication Errors**

1. Check your JIRA_PAT token is valid
2. Verify JIRA_BASE_URL is correct
3. Test connectivity:
   ```bash
   ./jira_triage/setup_and_run_polling.sh test-connectivity
   ```

### **Permissions Issues**

```bash
# Check log directory permissions
ls -la logs/

# Test log directory writability
touch logs/test_write && rm logs/test_write
```

## Running as a Service

### **Using systemd (Linux)**

Create `/etc/systemd/system/jira-polling.service`:
```ini
[Unit]
Description=Jira Polling Service
After=network.target

[Service]
Type=exec
User=your-username
WorkingDirectory=/path/to/Jira-triage
ExecStart=/path/to/Jira-triage/jira_triage/setup_and_run_polling.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable jira-polling.service
sudo systemctl start jira-polling.service
sudo systemctl status jira-polling.service
```

### **Using launchd (macOS)**

Create `~/Library/LaunchAgents/com.yourname.jira-polling.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.yourname.jira-polling</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/Jira-triage/jira_triage/setup_and_run_polling.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/Jira-triage</string>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/path/to/Jira-triage/logs/polling-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/Jira-triage/logs/polling-stderr.log</string>
</dict>
</plist>
```

Load and start:
```bash
launchctl load ~/Library/LaunchAgents/com.yourname.jira-polling.plist
launchctl start com.yourname.jira-polling
```

## Benefits vs Webhooks

- ✅ **No Jira Admin Required** - Works with regular user permissions
- ✅ **Identical Analysis Output** - Same comprehensive analysis as webhooks  
- ✅ **Flexible Scheduling** - Configurable poll intervals
- ✅ **Custom JQL Queries** - Target specific tickets or projects
- ✅ **Complete Monitoring** - Full logging and real-time monitoring
- ✅ **Reliable Operation** - No dependency on external connectivity to your machine

The polling service provides a complete alternative to webhooks while maintaining identical analysis quality and output format!