# Magnus API Log Download Integration

## Overview

Successfully implemented comprehensive Magnus API integration for downloading CPE device logs. The system follows a 5-step API flow and seamlessly integrates with CLI, webhook, and polling modes.

## Files Created

### 1. `jira_triage/magnus_log_client.py`
Core Magnus API client implementing the complete 5-step log download flow:

- **MagnusLogClient class** with:
  - `download_logs()` - Main entry point
  - `_get_uuid_from_mac()` - Deep Link API (Step 1)
  - `_get_cpe_id_from_uuid()` - CPE Info API (Step 2)
  - `_get_log_list()` - Log List API with pagination (Step 3)
  - `_download_log_files()` - Log Download API (Step 4)
  - `_make_request()` - Authenticated HTTP client with retries and error handling
  - `_extract_mac_from_description()` - MAC address extraction from Jira description

- **Features**:
  - Bearer token authentication
  - Automatic MAC address extraction from Jira description
  - Date range extraction from description using `date_extractor`
  - Pagination support (>100 logs)
  - Exponential backoff retry mechanism (3 attempts)
  - Comprehensive error handling for all API steps
  - Debug logging via `debug_log`
  - Non-blocking: failures don't interrupt triage workflow
  - Saves downloaded logs to `output_dir/logs/magnus/`

### 2. `jira_triage/log_merger.py`
Intelligent log merging module that processes downloaded archives:

- **Features**:
  - Automatically extracts nested `.zip` and `.tgz` archives
  - Categorizes logs by pattern/extension (errors, warnings, debug, text, json, etc.)
  - Merges logs of the same category with clean separators
  - Deduplicates logs based on MD5 hashing
  - Generates comprehensive metadata and summary reports

### 3. `jira_triage/date_extractor.py`
Intelligent date parsing module for Jira ticket descriptions:

- **`extract_dates_from_description()` function** supporting:
  - ISO 8601 format: `2026-05-08`, `2026-05-08T15:30:00`
  - Named months: `May 8, 2026`, `8 May 2026`, `May 8`
  - Relative dates: `today`, `yesterday`, `last 7 days`, `last 24 hours`
  - Mixed patterns: `from May 8 to May 10`
  
- **Features**:
  - Returns tuple of (start_date, end_date) as UTC datetimes
  - Automatically adds 1 day before start date for broader analysis
  - Falls back to last 7 days if no dates found
  - `format_date_for_api()` - Converts datetime to Magnus API format

## Files Modified

### 1. `jira_triage/config.py`
Added Magnus API configuration fields to `Config` dataclass:

```python
# New fields added to Config dataclass:
magnus_log_api_enabled: bool = False
magnus_log_api_base_url: str = "https://cms-cdn.yo-digital.com/hgw/magnus"
magnus_log_api_token: str = ""
magnus_log_mac_address: str | None = None
magnus_log_start_date: str | None = None
magnus_log_end_date: str | None = None
```

Added environment variable parsing in `load_config()`:
- `MAGNUS_LOG_API_ENABLED` - Enable/disable (default: false)
- `MAGNUS_LOG_API_BASE_URL` - API base URL (default: hardcoded)
- `MAGNUS_LOG_API_TOKEN` - Bearer token (required if enabled)
- `MAGNUS_LOG_MAC` - Override MAC address
- `MAGNUS_LOG_START_DATE` - Override start date (ISO 8601)
- `MAGNUS_LOG_END_DATE` - Override end date (ISO 8601)

### 2. `jira_triage/core.py`
Integrated Magnus log client into triage workflow:

**Imports added**:
```python
from .magnus_log_client import MagnusLogClient
```

**Changes to `triage()` function**:
- Added parameters: `magnus_log_mac`, `magnus_log_start_date`, `magnus_log_end_date`
- Added Magnus log download section (Phase 2.5) after local logs, before log processing
- Passes Jira ticket description for date/MAC extraction
- Respects CLI parameter overrides
- Non-fatal error handling with debug logging
- Returns `magnus_stats` for tracking download results

### 3. `jira_triage/cli.py`
Added Magnus API CLI parameters for developer control:

**New argument group**:
```
Magnus API arguments (optional log source)
  --magnus-log-mac MAC              Override MAC address
  --magnus-log-start-date DATE      Start date (ISO 8601)
  --magnus-log-end-date DATE        End date (ISO 8601)
```

**Updated triage() call**:
- Passes `magnus_log_mac`, `magnus_log_start_date`, `magnus_log_end_date` from CLI args

### 4. `.env.example`
Added Magnus API configuration section:

```bash
# Magnus API Log Downloading (Optional)
MAGNUS_LOG_API_ENABLED=false
MAGNUS_LOG_API_BASE_URL=https://cms-cdn.yo-digital.com/hgw/magnus
MAGNUS_LOG_API_TOKEN=your_bearer_token_here
MAGNUS_AUTO_MERGE_LOGS=true
MAGNUS_MERGE_OUTPUT_DIR=merged
# Optional developer overrides
MAGNUS_LOG_MAC=
MAGNUS_LOG_START_DATE=
MAGNUS_LOG_END_DATE=
```

## API & Merging Flow

### Step 1: Deep Link API
```
GET /v1/cpe/deep-link?cpeGenericFilter={"macAddress":"A0:8A:06:A8:82:DF"}
Response: {"uuid": "87bda2ed-439d-452e-a5af-31db1d62c464", ...}
```

### Step 2: CPE Info API
```
GET /v1/cpe?cpeGenericFilter={"filter":"87bda2ed-439d-452e-a5af-31db1d62c464"}
Response: {"cpeId": "68c2e547136f3a23580db0b9", ...}
```

### Step 3: Log List API (Paginated)
```
GET /v1/cpe/{cpeId}/logInfo?size=100&page=0&dateFilter={"startDate":"2026-05-04T00:00:00.000","endDate":"2026-05-08T23:59:00.000"}
Response: {"files": [{"id": "69f7c859f4046493987a4684"}, ...], "totalPages": 2, ...}
```

### Step 4: Log Download API
```
POST /v2/cpe/{cpeId}/logFile
{"cpeLogFileIds": ["id1", "id2", "id3", ...]}
Response: Downloaded log file content
```

### Step 5: Log Auto-Merge (Post-download)
```
1. Extracts all downloaded .tgz / .zip archives
2. Categorizes files (errors, debug, warnings, etc.)
3. Deduplicates identical log files using MD5 hashes
4. Merges logs into output files by category
5. Provides metadata and summary stats
```

## Usage

### Manual CLI Usage
```bash
# Using defaults from description and environment
jira-cursor TICKET-123

# Override MAC address
jira-cursor TICKET-123 --magnus-log-mac A0:8A:06:A8:82:DF

# Override date range
jira-cursor TICKET-123 \
  --magnus-log-start-date 2026-05-04 \
  --magnus-log-end-date 2026-05-08

# All parameters
jira-cursor TICKET-123 \
  --magnus-log-mac A0:8A:06:A8:82:DF \
  --magnus-log-start-date 2026-05-04 \
  --magnus-log-end-date 2026-05-08
```

### Webhook Integration
Automatically enabled if `MAGNUS_LOG_API_ENABLED=true` in environment.

### Polling Integration
Automatically enabled if `MAGNUS_LOG_API_ENABLED=true` in environment.

## Configuration

### Enable Magnus API
```bash
# In .env file
MAGNUS_LOG_API_ENABLED=true
MAGNUS_LOG_API_TOKEN=your_bearer_token_here
# Optional: override defaults
MAGNUS_LOG_MAC=A0:8A:06:A8:82:DF
MAGNUS_LOG_START_DATE=2026-05-04
MAGNUS_LOG_END_DATE=2026-05-08
```

### Date Extraction from Jira Description
The system automatically extracts dates from ticket description. Supported formats:
- **ISO 8601**: "2026-05-08", "2026-05-08T15:30:00"
- **Named months**: "May 8, 2026", "8 May 2026"
- **Relative**: "today", "yesterday", "last 7 days", "last 24 hours"

Example Jira descriptions:
```
"Issue from May 8 to May 10, 2026"
"Problem occurred on 2026-05-08"
"Last 24 hours of logs needed"
"Logs from 2026-05-04T00:00:00 to 2026-05-08T23:59:00"
```

## Error Handling

- **Missing token**: Skips Magnus logs, continues with other sources
- **Missing MAC**: Skips Magnus logs, continues with triage
- **Network errors**: Retries up to 3 times with exponential backoff
- **Invalid dates**: Falls back to last 7 days
- **API failures**: Logged but non-fatal, triage continues
- **401/403 errors**: Detected as authentication failure, clear error message

## Output Structure

Magnus logs and merged outputs are saved to:
`output_dir/TICKET/logs/magnus/` (raw downloads)
`output_dir/TICKET/logs/merged/` (merged log files)

Files created:
- `logs/magnus/magnus_logs_TICKET_TIMESTAMP.json` - Downloaded log metadata
- `logs/merged/errors_merged.log` - Aggregated error logs
- `logs/merged/debug_merged.log` - Aggregated debug logs
- `logs/merged/metadata/merge_summary.txt` - Merge process summary
- `logs.summary.json/md` - Selective signal extraction (from errors_merged.log)

## Key Design Decisions

1. **Non-blocking**: Magnus log failures don't interrupt triage workflow
2. **Opt-in**: Feature disabled by default (`MAGNUS_LOG_API_ENABLED=false`)
3. **Default URL**: Hardcoded Magnus API URL, only token needs configuration
4. **Flexible dates**: Extract from description or override via CLI/env
5. **Developer-friendly**: CLI parameters allow testing without modifying Jira
6. **Pagination**: Automatically handles > 100 logs with page iteration
7. **Token refresh**: Daily token update via environment variable
8. **Consistent patterns**: Mirrors existing `logs_client.py` architecture

## Testing

To test the implementation:

1. **Enable Magnus API**:
   ```bash
   export MAGNUS_LOG_API_ENABLED=true
   export MAGNUS_LOG_API_TOKEN=your_token
   ```

2. **Test CLI**:
   ```bash
   jira-cursor TICKET-123
   # Should download logs if MAC found in description or environment
   ```

3. **Test date extraction**:
   ```python
   from jira_triage.date_extractor import extract_dates_from_description
   start, end = extract_dates_from_description("Logs from May 8 to May 10, 2026")
   print(start, end)  # Should return datetime objects
   ```

4. **Test API flow**:
   ```python
   from jira_triage.config import load_config
   from jira_triage.magnus_log_client import MagnusLogClient
   
   cfg = load_config()
   client = MagnusLogClient(cfg)
   stats = client.download_logs("TICKET-123", mac_address="A0:8A:06:A8:82:DF")
   print(stats)  # Check success, logs_found, logs_downloaded
   ```

## Integration Points

- ✅ **CLI**: Full parameter support with `--magnus-log-*` options
- ✅ **Webhook**: Automatic integration via triage() function
- ✅ **Polling**: Automatic integration via triage() function
- ✅ **Config**: Environment variable support
- ✅ **Logging**: Debug logging for troubleshooting
- ✅ **Error handling**: Non-fatal failures with graceful fallback

## Implementation Status

All phases completed:
- ✅ Phase 1: Core API client
- ✅ Phase 2: Configuration updates
- ✅ Phase 3: Date extraction
- ✅ Phase 4: Triage integration
- ✅ Phase 5: CLI integration
- ✅ Phase 6: Webhook support (automatic)
- ✅ Phase 7: Polling support (automatic)
- ✅ Phase 8: Logging & monitoring
- ✅ Phase 9: Error handling & retries
- ✅ Phase 10: Documentation & .env updates
