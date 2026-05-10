# Magnus API Quick Start Guide

## What Was Implemented

A complete Magnus API integration that automatically downloads CPE device logs in your Jira triage workflow.

## Quick Setup

### 1. Enable in Environment
```bash
export MAGNUS_LOG_API_ENABLED=true
export MAGNUS_LOG_API_TOKEN=your_bearer_token_here
```

### 2. Add to .env File (Optional)
```bash
# In .env or .env.local
MAGNUS_LOG_API_ENABLED=true
MAGNUS_LOG_API_TOKEN=your_bearer_token_here
```

## How It Works

### Automatic Mode (Recommended)
1. Add MAC address to Jira ticket description: `A0:8A:06:A8:82:DF`
2. Add date range to description: `May 8 to May 10, 2026` or `2026-05-08 to 2026-05-08`
3. Run: `jira-cursor TICKET-123`
4. Logs automatically downloaded to `out/TICKET-123/logs/magnus/`

### Manual Mode (Developer Override)
```bash
# Override MAC address
jira-cursor TICKET-123 --magnus-log-mac A0:8A:06:A8:82:DF

# Override date range
jira-cursor TICKET-123 \
  --magnus-log-start-date 2026-05-04 \
  --magnus-log-end-date 2026-05-08

# Both
jira-cursor TICKET-123 \
  --magnus-log-mac A0:8A:06:A8:82:DF \
  --magnus-log-start-date 2026-05-04 \
  --magnus-log-end-date 2026-05-08
```

## Jira Description Examples

### Format 1: Named Dates
```
Device: A0:8A:06:A8:82:DF
Issue occurred between May 8 and May 10, 2026
Need logs for this period
```

### Format 2: ISO 8601
```
MAC: A0:8A:06:A8:82:DF
Date range: 2026-05-04 to 2026-05-08
```

### Format 3: Relative
```
Device MAC: A0:8A:06:A8:82:DF
Need last 24 hours of logs
```

### Format 4: Mixed
```
CPE MAC address is A0:8A:06:A8:82:DF
Log collection from 2026-05-08T00:00:00 to today
```

## Configuration Options

| Variable | Purpose | Required | Default |
|----------|---------|----------|---------|
| `MAGNUS_LOG_API_ENABLED` | Enable/disable feature | No | `false` |
| `MAGNUS_LOG_API_TOKEN` | Bearer token | Yes (if enabled) | - |
| `MAGNUS_LOG_API_BASE_URL` | API base URL | No | `https://cms-cdn.yo-digital.com/hgw/magnus` |
| `MAGNUS_LOG_MAC` | Default MAC address | No | - |
| `MAGNUS_LOG_START_DATE` | Default start date | No | - |
| `MAGNUS_LOG_END_DATE` | Default end date | No | - |

## Supported Date Formats

The system automatically extracts dates from your Jira description. Supports:

| Format | Examples |
|--------|----------|
| ISO 8601 | `2026-05-08`, `2026-05-08T15:30:00` |
| Named months | `May 8, 2026`, `8 May 2026`, `May 8` |
| Relative | `today`, `yesterday`, `last 7 days`, `last 24 hours` |

## Troubleshooting

### Logs Not Downloaded
1. Check if `MAGNUS_LOG_API_ENABLED=true`
2. Check if token is set: `echo $MAGNUS_LOG_API_TOKEN`
3. Check if MAC address is in description or set as parameter
4. Check output: `out/TICKET/logs/magnus/`

### MAC Address Not Found
- Add to description: `Device MAC: A0:8A:06:A8:82:DF`
- Or use CLI: `--magnus-log-mac A0:8A:06:A8:82:DF`

### Authentication Failed
- Verify token is correct: `MAGNUS_LOG_API_TOKEN=...`
- Token must be refreshed daily

### No Logs in Date Range
- Check date range in description or CLI parameters
- Verify device had logs during that period
- Try expanding date range

## Integration with Other Features

### Webhook
Automatically downloads Magnus logs when enabled:
```bash
# Webhook will use Magnus logs if enabled in .env
```

### Polling
Automatically downloads Magnus logs for each processed ticket:
```bash
jira-cursor poll --daemon
# Downloads Magnus logs for each new ticket
```

### Manual Processing
Always downloads Magnus logs if enabled:
```bash
jira-cursor TICKET-123
```

## Output Structure

```
out/
└── TICKET-123/
    ├── logs/
    │   └── magnus/
    │       └── magnus_logs_TICKET-123_1234567890.json
    ├── issue.json
    ├── context.md
    └── ... (other files)
```

## API Details

Magnus API follows a 4-step flow automatically:

1. **Deep Link**: MAC → UUID
2. **CPE Info**: UUID → CPE ID
3. **Log List**: CPE ID → List of log file IDs (paginated)
4. **Download**: CPE ID + IDs → Download logs

All steps are automatic and non-blocking. If any step fails, triage continues normally.

## Debugging

Enable debug logging:
```python
from jira_triage.debug_log import debug_log

# Logs written to DEBUG_LOG_PATH in config
# Check output for "magnus-log" entries
```

## Webhook Configuration

If using webhook integration:

```bash
# In .env
MAGNUS_LOG_API_ENABLED=true
MAGNUS_LOG_API_TOKEN=your_token
WEBHOOK_PORT=8080
WEBHOOK_HOST=0.0.0.0
```

Then webhook will automatically download Magnus logs for each received event.

## Polling Configuration

If using polling:

```bash
# In .env
MAGNUS_LOG_API_ENABLED=true
MAGNUS_LOG_API_TOKEN=your_token
JIRA_POLLING_ENABLED=true
JIRA_POLLING_INTERVAL=300
```

Then polling will automatically download Magnus logs for each new ticket.

## Next Steps

1. Set your bearer token in environment
2. Enable the feature: `MAGNUS_LOG_API_ENABLED=true`
3. Add MAC addresses to Jira descriptions (format: `A0:8A:06:A8:82:DF`)
4. Add date ranges to descriptions (automatic extraction)
5. Run triage normally - logs download automatically!

## Questions?

Refer to `MAGNUS_API_IMPLEMENTATION.md` for detailed technical documentation.
