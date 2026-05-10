"""Magnus API client for downloading CPE device logs."""

from __future__ import annotations

import json
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .config import Config
from .date_extractor import extract_dates_from_description, format_date_for_api
from .debug_log import debug_log
from .log_merger import merge_logs_by_category


class MagnusLogError(RuntimeError):
    pass


@dataclass(frozen=True)
class MagnusLogStats:
    success: bool
    uuid: str | None = None
    cpe_id: str | None = None
    logs_found: int = 0
    logs_downloaded: int = 0
    output_dir: Path | None = None
    merged_dir: Path | None = None
    merge_metadata: dict[str, Any] | None = None
    error: str | None = None


class MagnusLogClient:
    """Client for Magnus API log downloading."""
    
    def __init__(self, config: Config) -> None:
        self.config = config
        self.base_url = config.magnus_log_api_base_url.rstrip("/")
        self.token = config.magnus_log_api_token
        self.timeout = config.http_timeout_seconds
        self.verify_ssl = config.jira_verify_ssl
        self.max_retries = 3
        
    def _extract_tenant_code(self, issue_data: dict[str, Any]) -> str | None:
        """
        Extract the tenant (Natco) code from Jira issue fields.
        """
        mapping = {
            "Hungary": "hu", "HU (Hungary)": "hu",
            "Austria": "at", "AT (Austria)": "at",
            "Slovakia": "sk", "SK (Slovakia)": "sk",
            "Czech Republic": "cz", "CZ (Czech Republic)": "cz", "Czechia": "cz", "CZ (Czechia)": "cz",
            "Germany": "de", "DE (Germany)": "de",
            "Montenegro": "me", "ME (Montenegro)": "me",
            "Macedonia": "mk", "MK (Macedonia)": "mk",
            "Greece": "gr", "GR (Greece)": "gr",
            "Poland": "pl", "PL (Poland)": "pl",
            "Croatia": "hr", "HR (Croatia)": "hr"
        }
        
        fields = issue_data.get("fields", {})
        if not isinstance(fields, dict):
            return None
            
        def find_country(data: Any) -> str | None:
            if isinstance(data, dict):
                val = data.get("value")
                if isinstance(val, str):
                    for key, code in mapping.items():
                        if key.lower() in val.lower():
                            return code
                for v in data.values():
                    res = find_country(v)
                    if res: return res
            elif isinstance(data, list):
                for item in data:
                    res = find_country(item)
                    if res: return res
            elif isinstance(data, str):
                for key, code in mapping.items():
                    if key.lower() in data.lower():
                        return code
            return None
            
        return find_country(fields)

    def download_logs(
        self,
        ticket_key: str,
        mac_address: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        output_dir: Path | None = None,
        description: str | None = None,
        issue_data: dict[str, Any] | None = None,
        auto_merge_logs: bool = True,
    ) -> MagnusLogStats:
        """
        Download logs from Magnus API for a CPE device.
        
        Flow:
        1. Deep Link API: Get UUID from MAC address
        2. CPE Info API: Get CPE ID from UUID
        3. Log List API: Get list of log files with pagination
        4. Log Download API: Download all log files
        
        Args:
            ticket_key: Jira ticket key (for logging)
            mac_address: MAC address of CPE (or None to extract from description)
            start_date: Start date for log filtering
            end_date: End date for log filtering
            output_dir: Directory to save logs
            description: Jira ticket description (for date/MAC extraction)
            
        Returns:
            MagnusLogStats with success/failure info
        """
        
        if not self.token:
            return MagnusLogStats(
                success=False,
                error="Magnus API token not configured (MAGNUS_LOG_API_TOKEN)"
            )
        
        try:
            # Determine MAC address
            mac = mac_address or self.config.magnus_log_mac_address
            if not mac and description:
                mac = self._extract_mac_from_description(description)
            
            if not mac:
                debug_log(
                    run_id="magnus-log",
                    hypothesis_id="mac-extraction",
                    location="jira_triage/magnus_log_client.py:download_logs",
                    message="No MAC address found",
                    data={"ticket_key": ticket_key},
                )
                return MagnusLogStats(
                    success=False,
                    error="No MAC address found in configuration or ticket description"
                )
            
            # Determine date range
            if start_date is None or end_date is None:
                if description:
                    start_date, end_date = extract_dates_from_description(description)
                else:
                    from .date_extractor import _default_date_range
                    start_date, end_date = _default_date_range()
            
            # Override with explicit dates if provided
            if self.config.magnus_log_start_date:
                try:
                    start_date = datetime.fromisoformat(self.config.magnus_log_start_date.replace("Z", "+00:00"))
                except Exception:
                    pass
            
            if self.config.magnus_log_end_date:
                try:
                    end_date = datetime.fromisoformat(self.config.magnus_log_end_date.replace("Z", "+00:00"))
                except Exception:
                    pass
            
            output_dir = output_dir or Path(self.config.output_dir) / "logs" / "magnus"
            output_dir.mkdir(parents=True, exist_ok=True)
            
            debug_log(
                run_id="magnus-log",
                hypothesis_id="start",
                location="jira_triage/magnus_log_client.py:download_logs",
                message="Starting Magnus log download",
                data={
                    "ticket_key": ticket_key,
                    "mac_address": mac,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "output_dir": str(output_dir),
                },
            )
            
            # Step 0: Set Tenant Session (if applicable)
            if issue_data:
                tenant_code = self._extract_tenant_code(issue_data)
                if tenant_code:
                    try:
                        self._make_request(
                            "PUT",
                            "/session/v1/tenant",
                            data={"tenantCode": tenant_code}
                        )
                        debug_log(
                            run_id="magnus-log",
                            hypothesis_id="step0",
                            location="jira_triage/magnus_log_client.py:download_logs",
                            message=f"Step 0 complete: set tenant session to {tenant_code}",
                            data={"tenantCode": tenant_code},
                        )
                    except Exception as e:
                        debug_log(
                            run_id="magnus-log",
                            hypothesis_id="step0_error",
                            location="jira_triage/magnus_log_client.py:download_logs",
                            message=f"Failed to set tenant session: {e}",
                            data={"tenantCode": tenant_code},
                        )
                        # We continue anyway, as it might still work if the user has only one role

            # Step 1: Get UUID from MAC address
            uuid = self._get_uuid_from_mac(mac)
            if not uuid:
                return MagnusLogStats(
                    success=False,
                    error=f"Failed to get UUID from MAC address: {mac}"
                )
            
            debug_log(
                run_id="magnus-log",
                hypothesis_id="step1",
                location="jira_triage/magnus_log_client.py:download_logs",
                message="Step 1 complete: got UUID",
                data={"uuid": uuid},
            )
            
            # Step 2: Get CPE ID from UUID
            cpe_id = self._get_cpe_id_from_uuid(uuid)
            if not cpe_id:
                return MagnusLogStats(
                    success=False,
                    uuid=uuid,
                    error=f"Failed to get CPE ID from UUID: {uuid}"
                )
            
            debug_log(
                run_id="magnus-log",
                hypothesis_id="step2",
                location="jira_triage/magnus_log_client.py:download_logs",
                message="Step 2 complete: got CPE ID",
                data={"cpe_id": cpe_id},
            )
            
            # Step 3: Get list of logs with pagination
            log_ids = self._get_log_list(cpe_id, start_date, end_date)
            if not log_ids:
                debug_log(
                    run_id="magnus-log",
                    hypothesis_id="step3",
                    location="jira_triage/magnus_log_client.py:download_logs",
                    message="No logs found in date range",
                    data={
                        "cpe_id": cpe_id,
                        "start_date": start_date.isoformat(),
                        "end_date": end_date.isoformat(),
                    },
                )
                return MagnusLogStats(
                    success=True,
                    uuid=uuid,
                    cpe_id=cpe_id,
                    logs_found=0,
                    logs_downloaded=0,
                    output_dir=output_dir,
                )
            
            debug_log(
                run_id="magnus-log",
                hypothesis_id="step3",
                location="jira_triage/magnus_log_client.py:download_logs",
                message="Step 3 complete: got log list",
                data={
                    "logs_found": len(log_ids),
                    "log_ids": log_ids[:10],  # First 10 for logging
                },
            )
            
            # Step 4: Download log files
            downloaded = self._download_log_files(cpe_id, log_ids, output_dir, ticket_key)
            
            debug_log(
                run_id="magnus-log",
                hypothesis_id="step4",
                location="jira_triage/magnus_log_client.py:download_logs",
                message="Step 4 complete: downloaded files",
                data={
                    "total_logs": len(log_ids),
                    "downloaded": downloaded,
                },
            )
            
            merge_metadata = None
            merged_dir = None
            if auto_merge_logs and downloaded > 0:
                # Merge into ticket_dir/logs/merged/
                merged_dir = output_dir.parent / getattr(self.config, "magnus_merge_output_dir", "merged")
                try:
                    merge_metadata = merge_logs_by_category(output_dir, merged_dir)
                    debug_log(
                        run_id="magnus-log",
                        hypothesis_id="step5_merge",
                        location="jira_triage/magnus_log_client.py:download_logs",
                        message="Step 5 complete: merged logs",
                        data={"merged_dir": str(merged_dir), "stats": merge_metadata.get("statistics", {})},
                    )
                except Exception as e:
                    debug_log(
                        run_id="magnus-log",
                        hypothesis_id="step5_merge_error",
                        location="jira_triage/magnus_log_client.py:download_logs",
                        message=f"Failed to merge logs: {e}",
                        data={"error": str(e)},
                    )
            
            return MagnusLogStats(
                success=True,
                uuid=uuid,
                cpe_id=cpe_id,
                logs_found=len(log_ids),
                logs_downloaded=downloaded,
                output_dir=output_dir,
                merged_dir=merged_dir,
                merge_metadata=merge_metadata,
            )
            
        except Exception as e:
            # #region agent log
            try:
                import json as _json, time as _time
                _log_entry = {
                    "sessionId": "d70c83",
                    "id": f"log_{int(_time.time() * 1000)}_download_error",
                    "timestamp": int(_time.time() * 1000),
                    "location": "jira_triage/magnus_log_client.py:331",
                    "message": "Error in download_logs",
                    "data": {"error": str(e), "error_type": type(e).__name__},
                    "runId": "debug_run_1",
                    "hypothesisId": "H1"
                }
                with open("/Users/abijithp/Desktop/Jira-triage/.cursor/debug-d70c83.log", "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps(_log_entry) + "\n")
            except Exception: pass
            # #endregion
            debug_log(
                run_id="magnus-log",
                hypothesis_id="error",
                location="jira_triage/magnus_log_client.py:download_logs",
                message="Error in Magnus log download",
                data={
                    "ticket_key": ticket_key,
                    "error": str(e),
                },
            )
            return MagnusLogStats(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )
    
    def _ssl_context(self) -> ssl.SSLContext | None:
        """Get SSL context based on verification settings."""
        if self.verify_ssl:
            return None
        return ssl._create_unverified_context()
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
        query_params: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Make authenticated request to Magnus API with retries."""
        
        url = f"{self.base_url}{endpoint}"
        if query_params:
            url += "?" + urlencode(query_params, quote_via=quote)
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Safari/605.1.15",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Origin": "https://magnus-ui-shared.hgw.yo-digital.com",
            "Referer": "https://magnus-ui-shared.hgw.yo-digital.com/",
        }
        
        # #region agent log
        try:
            import json as _json, time as _time
            _log_payload = {
                "sessionId": "3c58ce",
                "runId": "debug_run_1",
                "hypothesisId": "A,C",
                "location": "jira_triage/magnus_log_client.py:406",
                "message": "Step 3: Final request details",
                "data": {
                    "method": method,
                    "url": url,
                    "headers": {k: v for k, v in headers.items() if k != "Authorization"}
                },
                "timestamp": int(_time.time() * 1000)
            }
            with open("/Users/abijithp/Desktop/Jira-triage/.cursor/debug-3c58ce.log", "a", encoding="utf-8") as _f:
                _f.write(_json.dumps(_log_payload) + "\n")
        except Exception: pass
        # #endregion

        request_data: bytes | None = None
        if data:
            request_data = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        
        for attempt in range(self.max_retries):
            try:
                debug_log(
                    run_id="magnus-api",
                    hypothesis_id="request",
                    location="jira_triage/magnus_log_client.py:_make_request",
                    message=f"Magnus API Request: {method} {endpoint}",
                    data={
                        "method": method,
                        "url": url,
                        "query_params": query_params,
                        "has_data": data is not None,
                    },
                )
                req = Request(url, data=request_data, headers=headers, method=method)
                
                with urlopen(
                    req,
                    timeout=self.timeout,
                    context=self._ssl_context(),
                ) as resp:
                    body = resp.read()
                    if not body:
                        debug_log(
                            run_id="magnus-api",
                            hypothesis_id="response",
                            location="jira_triage/magnus_log_client.py:_make_request",
                            message=f"Magnus API Empty Response for {endpoint}",
                            data={"endpoint": endpoint, "status": resp.status},
                        )
                        return {}
                    
                    response_json = json.loads(body.decode("utf-8"))
                    
                    # #region agent log
                    try:
                        import json as _json, time as _time
                        _log_payload = {
                            "sessionId": "3c58ce",
                            "runId": "debug_run_1",
                            "hypothesisId": "D",
                            "location": "jira_triage/magnus_log_client.py:444",
                            "message": "Step 3: Received response",
                            "data": {
                                "status": resp.status,
                                "body": response_json
                            },
                            "timestamp": int(_time.time() * 1000)
                        }
                        with open("/Users/abijithp/Desktop/Jira-triage/.cursor/debug-3c58ce.log", "a", encoding="utf-8") as _f:
                            _f.write(_json.dumps(_log_payload) + "\n")
                    except Exception: pass
                    # #endregion

                    debug_log(
                        run_id="magnus-api",
                        hypothesis_id="response",
                        location="jira_triage/magnus_log_client.py:_make_request",
                        message=f"Magnus API Response for {endpoint}",
                        data={
                            "endpoint": endpoint,
                            "status": resp.status,
                            "response": response_json
                        },
                    )
                    
                    return response_json
                    
            except HTTPError as e:
                status_code = e.code
                try:
                    err_body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    err_body = "Could not read error body"

                # #region agent log
                try:
                    import json as _json, time as _time
                    _log_payload = {
                        "sessionId": "3c58ce",
                        "runId": "debug_run_1",
                        "hypothesisId": "D",
                        "location": "jira_triage/magnus_log_client.py:470",
                        "message": "Step 3: Received HTTP Error in _make_request",
                        "data": {
                            "status": status_code,
                            "body": err_body
                        },
                        "timestamp": int(_time.time() * 1000)
                    }
                    with open("/Users/abijithp/Desktop/Jira-triage/.cursor/debug-3c58ce.log", "a", encoding="utf-8") as _f:
                        _f.write(_json.dumps(_log_payload) + "\n")
                except Exception: pass
                # #endregion

                debug_log(
                    run_id="magnus-api",
                    hypothesis_id="error",
                    location="jira_triage/magnus_log_client.py:_make_request",
                    message=f"Magnus API HTTP Error {status_code} for {endpoint}",
                    data={
                        "endpoint": endpoint,
                        "status_code": status_code,
                        "error_body": err_body,
                    },
                )

                if status_code == 401 or status_code == 403:
                    raise MagnusLogError(f"Authentication failed ({status_code}): Invalid token or insufficient permissions. Body: {err_body}")
                
                if attempt < self.max_retries - 1 and status_code >= 500:
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
                    continue
                
                raise MagnusLogError(f"HTTP {status_code}: {err_body}")
                
            except URLError as e:
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
                    continue
                raise MagnusLogError(f"Network error: {str(e)}")
        
        return None
    
    def _download_binary(
        self,
        endpoint: str,
        data: dict[str, Any],
        output_path: Path,
    ) -> bool:
        """Make authenticated request to Magnus API and save binary response to file."""
        url = f"{self.base_url}{endpoint}"
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/octet-stream, application/json, */*",
            "User-Agent": "jira-triage/0.1",
            "Content-Type": "application/json",
        }
        
        request_data = json.dumps(data).encode("utf-8")
        
        for attempt in range(self.max_retries):
            try:
                debug_log(
                    run_id="magnus-api",
                    hypothesis_id="binary-request",
                    location="jira_triage/magnus_log_client.py:_download_binary",
                    message=f"Magnus API Binary Request: POST {endpoint}",
                    data={
                        "url": url,
                        "payload": data,
                    },
                )
                req = Request(url, data=request_data, headers=headers, method="POST")
                
                with urlopen(
                    req,
                    timeout=self.timeout,
                    context=self._ssl_context(),
                ) as resp:
                    with open(output_path, "wb") as f:
                        while True:
                            chunk = resp.read(8192)
                            if not chunk:
                                break
                            f.write(chunk)
                    
                    debug_log(
                        run_id="magnus-api",
                        hypothesis_id="binary-response",
                        location="jira_triage/magnus_log_client.py:_download_binary",
                        message=f"Magnus API Binary Download Success: {endpoint}",
                        data={
                            "endpoint": endpoint,
                            "status": resp.status,
                            "output_path": str(output_path),
                            "file_size": output_path.stat().st_size if output_path.exists() else 0,
                        },
                    )
                    return True
                    
            except HTTPError as e:
                status_code = e.code
                try:
                    err_body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    err_body = "Could not read error body"

                debug_log(
                    run_id="magnus-api",
                    hypothesis_id="binary-error",
                    location="jira_triage/magnus_log_client.py:_download_binary",
                    message=f"Magnus API Binary HTTP Error {status_code} for {endpoint}",
                    data={
                        "endpoint": endpoint,
                        "status_code": status_code,
                        "error_body": err_body,
                    },
                )

                if status_code == 401 or status_code == 403:
                    raise MagnusLogError(f"Authentication failed ({status_code}): Invalid token or insufficient permissions. Body: {err_body}")
                
                if attempt < self.max_retries - 1 and status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                
                raise MagnusLogError(f"HTTP {status_code}: {err_body}")
                
            except URLError as e:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise MagnusLogError(f"Network error: {str(e)}")
        
        return False

    def _get_uuid_from_mac(self, mac_address: str) -> str | None:
        """
        API Step 1: Get UUID from MAC address using Deep Link API.
        
        GET /v1/cpe/deep-link?cpeGenericFilter={"macAddress":"A0:8A:06:A8:82:DF"}
        """
        try:
            filter_json = json.dumps({"macAddress": mac_address}, separators=(',', ':'))
            response = self._make_request(
                "GET",
                "/v1/cpe/deep-link",
                query_params={"cpeGenericFilter": filter_json},
            )
            
            debug_log(
                run_id="magnus-log",
                hypothesis_id="step1-response",
                location="jira_triage/magnus_log_client.py:_get_uuid_from_mac",
                message="Step 1 response received",
                data={"mac_address": mac_address, "response": response},
            )

            if not response:
                return None
            
            uuid = response.get("info", {}).get("uuid")
            return uuid
            
        except MagnusLogError as e:
            debug_log(
                run_id="magnus-log",
                hypothesis_id="deeplink-api",
                location="jira_triage/magnus_log_client.py:_get_uuid_from_mac",
                message=f"Deep Link API error: {str(e)}",
                data={"mac_address": mac_address},
            )
            return None
    
    def _get_cpe_id_from_uuid(self, uuid: str) -> str | None:
        """
        API Step 2: Get CPE ID from UUID.
        
        GET /v1/cpe?cpeGenericFilter={"filter":"87bda2ed-439d-452e-a5af-31db1d62c464"}
        """
        try:
            filter_json = json.dumps({"filter": uuid}, separators=(',', ':'))
            response = self._make_request(
                "GET",
                "/v1/cpe",
                query_params={"cpeGenericFilter": filter_json},
            )
            
            debug_log(
                run_id="magnus-log",
                hypothesis_id="step2-response",
                location="jira_triage/magnus_log_client.py:_get_cpe_id_from_uuid",
                message="Step 2 response received",
                data={"uuid": uuid, "response": response},
            )

            if not response:
                return None
            
            cpe_id = response.get("info", {}).get("cpeId")
            return cpe_id
            
        except MagnusLogError as e:
            debug_log(
                run_id="magnus-log",
                hypothesis_id="cpe-info-api",
                location="jira_triage/magnus_log_client.py:_get_cpe_id_from_uuid",
                message=f"CPE Info API error: {str(e)}",
                data={"uuid": uuid},
            )
            return None
    
    def _get_log_list(self, cpe_id: str, start_date: datetime, end_date: datetime) -> list[str]:
        """
        API Step 3: Get paginated list of log file IDs.
        
        GET /v1/cpe/{cpeId}/logInfo?size=100&page=0&dateFilter={"startDate":"...","endDate":"..."}
        """
        log_ids: list[str] = []
        page = 0
        page_size = 50
        
        # Ensure end_date covers the full day (23:59:59) to match working example
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=0)
        
        try:
            while True:
                date_filter_dict = {
                    "startDate": format_date_for_api(start_date),
                    "endDate": format_date_for_api(end_date),
                }
                # Use separators to ensure no spaces in JSON, matching working example
                date_filter = json.dumps(date_filter_dict, separators=(',', ':'))
                
                # #region agent log
                try:
                    import json as _json, time as _time
                    _log_payload = {
                        "sessionId": "3c58ce",
                        "runId": "debug_run_1",
                        "hypothesisId": "A,B",
                        "location": "jira_triage/magnus_log_client.py:657",
                        "message": "Step 3: Preparing log list request",
                        "data": {
                            "cpe_id": cpe_id,
                            "date_filter_raw": date_filter,
                            "page_size": page_size
                        },
                        "timestamp": int(_time.time() * 1000)
                    }
                    with open("/Users/abijithp/Desktop/Jira-triage/.cursor/debug-3c58ce.log", "a", encoding="utf-8") as _f:
                        _f.write(_json.dumps(_log_payload) + "\n")
                except Exception: pass
                # #endregion

                debug_log(
                    run_id="magnus-log",
                    hypothesis_id="log-list-request",
                    location="jira_triage/magnus_log_client.py:_get_log_list",
                    message=f"Fetching log list for CPE {cpe_id}, page {page}",
                    data={
                        "cpe_id": cpe_id,
                        "page": page,
                        "date_filter": date_filter_dict,
                    },
                )

                response = self._make_request(
                    "GET",
                    f"/v1/cpe/{cpe_id}/logInfo",
                    query_params={
                        "size": str(page_size),
                        "page": str(page),
                        "dateFilter": date_filter,
                    },
                )
                
                debug_log(
                    run_id="magnus-log",
                    hypothesis_id="step3-response",
                    location="jira_triage/magnus_log_client.py:_get_log_list",
                    message=f"Step 3 response received for page {page}",
                    data={
                        "cpe_id": cpe_id,
                        "page": page,
                        "response": response,
                    },
                )

                if not response or "info" not in response:
                    break
                
                files = response.get("info", {}).get("files", [])
                if not files:
                    break
                
                for file_info in files:
                    file_id = file_info.get("id")
                    if file_id:
                        log_ids.append(file_id)
                
                page += 1
                
        except MagnusLogError as e:
            debug_log(
                run_id="magnus-log",
                hypothesis_id="log-list-api",
                location="jira_triage/magnus_log_client.py:_get_log_list",
                message=f"Log List API error: {str(e)}",
                data={
                    "cpe_id": cpe_id,
                    "page": page,
                },
            )
        
        return log_ids
    
    def _download_log_files(
        self,
        cpe_id: str,
        log_ids: list[str],
        output_dir: Path,
        ticket_key: str,
    ) -> int:
        """
        API Step 4: Download log files.
        
        POST /v2/cpe/{cpeId}/logFile
        {"cpeLogFileIds": ["id1", "id2", ...]}
        """
        if not log_ids:
            return 0
        
        downloaded = 0
        
        try:
            # Save response to a binary tgz file
            log_file = output_dir / f"magnus_logs_{ticket_key}_{int(time.time())}.tgz"
            
            success = self._download_binary(
                f"/v2/cpe/{cpe_id}/logFile",
                data={"cpeLogFileIds": log_ids},
                output_path=log_file,
            )
            
            debug_log(
                run_id="magnus-log",
                hypothesis_id="step4-response",
                location="jira_triage/magnus_log_client.py:_download_log_files",
                message="Step 4 download attempt complete",
                data={
                    "cpe_id": cpe_id,
                    "log_ids_count": len(log_ids),
                    "success": success,
                    "output_path": str(log_file),
                },
            )

            if not success:
                return 0
            
            downloaded = len(log_ids)
            
            debug_log(
                run_id="magnus-log",
                hypothesis_id="download-api",
                location="jira_triage/magnus_log_client.py:_download_log_files",
                message="Downloaded log files",
                data={
                    "cpe_id": cpe_id,
                    "log_file": str(log_file),
                    "logs_count": downloaded,
                },
            )
            
        except MagnusLogError as e:
            debug_log(
                run_id="magnus-log",
                hypothesis_id="download-api-error",
                location="jira_triage/magnus_log_client.py:_download_log_files",
                message=f"Log Download API error: {str(e)}",
                data={"cpe_id": cpe_id},
            )
        
        return downloaded
    
    def _extract_mac_from_description(self, description: str) -> str | None:
        """Extract MAC address from description if present."""
        import re
        # MAC address pattern: XX:XX:XX:XX:XX:XX or XX-XX-XX-XX-XX-XX
        mac_pattern = r'([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})'
        match = re.search(mac_pattern, description)
        return match.group(0) if match else None
