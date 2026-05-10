# Jira-Triage Architecture

**Token-Optimized Flow Document**

## Data Flow
```mermaid
flowchart TD
    cli["CLI / Manual"] --> triage["core.py: triage()"]
    poll["Polling"] --> dup{"Duplicate?"}
    web["Webhook"] --> dup
    dup -->|"Yes"| skip["Skip"]
    dup -->|"No"| triage
    
    triage --> config["Load Config & Repo"]
    config --> fetch["Fetch Issue (REST/MCP)"]
    
    fetch --> logs{"Fetch Logs?"}
    logs -->|"Local/API"| log_fetch["Fetch (API/Local)"]
    logs -->|"Magnus"| mag_api["MagnusLogClient"]
    logs -->|"No"| kw["Extract Keywords"]
    
    mag_api --> mag_merge{"Auto-Merge?"}
    mag_merge -->|"Yes"| merge["log_merger.py: Extract, Dedup, Merge"]
    mag_merge -->|"No"| kw
    
    log_fetch --> proc["logs_processing.py"]
    merge -->|"errors_merged.log"| proc
    
    proc --> kw["repo.py: Extract Keywords"]
    kw --> path["Suggest Repo Paths"]
    path --> template["Create Analysis Template"]
    template --> ctx["Build Context Bundle"]
    
    ctx --> attach{"Attach?"}
    attach -->|"Yes"| up["Upload & Mark Processed"]
    attach -->|"No"| ai{"Run AI?"}
    up --> ai
    
    ai -->|"Yes"| cursor_ai["Run Cursor Analysis"]
    ai -->|"No"| open{"Open IDE?"}
    cursor_ai --> open
    
    open -->|"Yes"| ide["Launch Cursor"]
    open -->|"No"| done["Return TriageResult"]
    ide --> done
```

## Key Components

| Component | File(s) | Description |
|-----------|---------|-------------|
| **Entry** | `cli.py`, `webhook.py`, `polling_service.py` | Routes to orchestrator. Polling uses JQL `assignee=currentUser()`. |
| **Core** | `core.py` | Orchestrates fetch, process, analysis, and output generation. |
| **Jira** | `jira_client.py`, `jira_mcp.py`, `jira_attachments.py` | REST API (w/ token fallback), MCP fallback, attachment upload. |
| **Dedup** | `duplicate_detection.py`, `processed_tickets.py` | SQLite cache & attachment-based duplicate prevention. |
| **Logs** | `magnus_log_client.py`, `logs_client.py`, `logs_local.py` | Fetches device logs. Magnus extracts MAC/Dates from description. |
| **Merge** | `log_merger.py` | Extracts nested archives, categorizes, deduplicates (MD5), merges. |
| **Process**| `logs_processing.py` | Cleans logs, extracts signals (CPU, Memory, Errors, Stacks). |
| **Repo** | `repo.py`, `date_extractor.py` | Keyword/date extraction, suggests relevant source code paths. |
| **Context**| `context_builder.py`, `cursor_analysis.py` | Creates context bundle, runs optional AI agent analysis. |

## Output Structure (`out/<TICKET-KEY>/`)
- `issue.json`, `jira_source.json`, `jira_preflight.json`, `repo_paths.json`
- `logs.summary.json/md`, `logs.cleaned.txt`
- `logs/magnus/` (raw), `logs/merged/` (categorized & merged by `log_merger.py`)
- `analysis.md` (user workspace), `context.md`, `bundle.zip`
- `.cursor/context/TICKET.md` (IDE context)

## Configuration Priorities
1. **CLI Args**: `--repo`, `--no-magnus-merge`, `--process-logs`
2. **`.env` vars**: `JIRA_BASE_URL`, `MAGNUS_AUTO_MERGE_LOGS`, `JIRA_POLLING_INTERVAL`
3. **MCP Config**: `~/.cursor/mcp.json`

## Error Handling
- `ConfigError`: Setup issues (fatal)
- `TriageError`: Processing failures (fatal)
- `JiraError`: API/Auth issues (fatal if no fallback)
- Non-fatal: Log fetching/merging, AI analysis, or attachment uploads.