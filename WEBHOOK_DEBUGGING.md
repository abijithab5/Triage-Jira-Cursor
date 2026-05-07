# Webhook Authentication and Request Monitoring

This document describes the comprehensive logging and monitoring system implemented for debugging Jira authentication failures and webhook request reception issues.

## 🎯 What This Solves

Your original issues:
1. **Webhook auth should fail** - but you need to verify if it actually does
2. **Need to check webhook login** - verify if webhook can authenticate with Jira
3. **Need notification logs** - see if any input is received from Jira webhooks

## 📊 New Monitoring Capabilities

### 1. Webhook Request Logging
- **Every incoming request** is logged with correlation IDs
- **Request duration** tracking
- **Client IP and User-Agent** logging
- **Payload structure analysis** (without sensitive data)
- **Response correlation** - match requests to generated files

### 2. Authentication Monitoring
- **Detailed auth setup** logging with token validation
- **Jira connectivity classification** (DNS, network, auth, server errors)
- **Preflight check results** for both `/myself` and `/serverInfo` endpoints
- **Bearer vs Basic auth** mode tracking
- **Real-time auth failure** diagnostics

### 3. Error Classification System
- **Network errors**: DNS, connection refused, timeouts, SSL issues
- **Auth errors**: 401 (unauthorized), 403 (forbidden)
- **Client errors**: 400, 404 (bad request, not found)
- **Server errors**: 500+ (Jira server issues)

## 🚀 Quick Start

### 1. Start the Webhook with Enhanced Logging

```bash
# Activate virtual environment
source venv/bin/activate

# Start webhook server (creates logs automatically)
./jira_triage/setup_and_run_webhook.sh
```

### 2. Monitor in Real-Time

Open separate terminals and run:

```bash
# Monitor all webhook requests
./jira_triage/setup_and_run_webhook.sh monitor-webhook

# Monitor authentication attempts  
./jira_triage/setup_and_run_webhook.sh monitor-auth

# Monitor everything simultaneously
./jira_triage/setup_and_run_webhook.sh monitor-all

# Test Jira connectivity (without auth)
./jira_triage/setup_and_run_webhook.sh test-connectivity
```

## 📁 Log File Locations

All logs are stored in `logs/` directory:

- **`logs/webhook.log`** - HTTP requests, responses, processing time
- **`logs/auth.log`** - Authentication attempts, Jira API calls, errors
- **`logs/debug.log`** - Internal debug information
- **`logs/app.log`** - General application events

## 🔍 Debugging Your Specific Issues

### Issue 1: "Webhook auth should fail because in another network"

**What to monitor:**
```bash
# Start webhook and monitor auth attempts
./jira_triage/setup_and_run_webhook.sh &
./jira_triage/setup_and_run_webhook.sh monitor-auth
```

**What you'll see if auth fails:**
```
AUTH - ERROR - Probe GET network error: url=https://jira.../rest/api/2/myself error_type=dns_error error=...
AUTH - ERROR - Preflight failed: both /myself and /serverInfo failed
```

**What you'll see if auth unexpectedly succeeds:**
```
AUTH - INFO - Probe GET success: url=https://jira.../rest/api/2/myself status=200 seraph_reason=none has_username=true
AUTH - INFO - Preflight successful: both /myself and /serverInfo accessible
```

### Issue 2: "Check if webhook receives input from Jira"

**What to monitor:**
```bash
# Monitor webhook requests in real-time
./jira_triage/setup_and_run_webhook.sh monitor-webhook
```

**What you'll see when Jira sends requests:**
```
WEBHOOK - INFO - Request started: correlation_id=a1b2c3d4 method=POST path=/jira client_ip=10.0.0.100 user_agent=Atlassian-HttpClient/...
WEBHOOK - INFO - Processing payload: correlation_id=a1b2c3d4 ticket_key=PROJ-123 payload_type=dict
WEBHOOK - INFO - Request completed: correlation_id=a1b2c3d4 status_code=200 duration=2.451s
```

**What you'll see if no requests come in:**
```
(No webhook log entries - indicates Jira isn't reaching your server)
```

### Issue 3: "Test connectivity without depending on webhook"

```bash
# Test basic connectivity
./jira_triage/setup_and_run_webhook.sh test-connectivity
```

This will show:
- Whether your network can reach the Jira base URL
- Whether the `/serverInfo` endpoint is accessible
- Network-level connectivity issues

## 🛠 Configuration Options

Add these to your `.env` file to customize logging:

```bash
# Log levels: DEBUG, INFO, WARNING, ERROR
WEBHOOK_LOG_LEVEL=INFO
AUTH_LOG_LEVEL=INFO
APP_LOG_LEVEL=INFO

# Custom log file paths
WEBHOOK_LOG_FILE=logs/webhook.log
AUTH_LOG_FILE=logs/auth.log
DEBUG_LOG_PATH=logs/debug.log

# Session identification
DEBUG_SESSION_ID=my-debug-session
```

## 📋 Correlation IDs

Every webhook request gets a unique 8-character correlation ID (e.g., `a1b2c3d4`) that appears in:
- Webhook request logs
- Authentication attempt logs  
- Output file generation logs
- HTTP response headers (`X-Correlation-ID`)

This lets you trace a single Jira webhook from receipt through to file generation.

## 🔧 Troubleshooting Common Issues

### No webhook logs appearing
1. Check if webhook server is running: `ps aux | grep uvicorn`
2. Check log file permissions: `ls -la logs/`
3. Verify Jira webhook configuration points to your server

### Auth logs show network errors
1. Run connectivity test: `./jira_triage/setup_and_run_webhook.sh test-connectivity`
2. Check if you're on VPN/network that blocks Jira access
3. Verify `JIRA_BASE_URL` in your `.env` file

### Auth logs show 401/403 errors  
1. Check `JIRA_PAT` token is valid
2. Verify `JIRA_AUTH_MODE` setting (bearer vs basic)
3. Check if token has sufficient permissions

## 📈 Example Debugging Session

```bash
# Terminal 1: Start webhook server
source venv/bin/activate
./jira_triage/setup_and_run_webhook.sh

# Terminal 2: Monitor webhook requests
./jira_triage/setup_and_run_webhook.sh monitor-webhook

# Terminal 3: Monitor auth attempts  
./jira_triage/setup_and_run_webhook.sh monitor-auth

# Now trigger a webhook from Jira or curl:
curl -X POST http://localhost:8080/jira \
  -H "Content-Type: application/json" \
  -d '{"issue": {"key": "TEST-123"}}'
```

You'll see the complete flow:
1. **Webhook logs**: Request received, ticket key extracted
2. **Auth logs**: Authentication attempt, Jira API calls
3. **Webhook logs**: Processing completed, files created

This gives you complete visibility into whether the issue is network connectivity, authentication, or something else entirely.