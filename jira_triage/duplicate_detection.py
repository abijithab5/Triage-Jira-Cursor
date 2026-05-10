"""
Unified duplicate detection for Jira ticket processing.

This module provides centralized logic to determine whether a Jira ticket
has already been processed by checking for our uploaded analysis bundle
attachments and maintaining a local cache for performance.
"""

from enum import Enum
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

from .config import Config
from .debug_log import debug_log
from .jira_attachments import check_our_attachment_exists, get_our_attachment_info, JiraAttachment
from .processed_tickets import get_processed_tickets_db, ProcessedTicket


class ProcessingStatus(Enum):
    """Status of ticket processing check."""
    NOT_PROCESSED = "not_processed"           # No analysis found, needs processing
    PROCESSED_BY_US = "processed_by_us"       # Our attachment exists in Jira
    CACHE_HIT = "cache_hit"                   # Found in local DB, skipped Jira check
    ATTACHMENT_MISSING = "attachment_missing"  # Cache says processed but Jira attachment gone
    ERROR = "error"                           # Error during detection process


@dataclass
class ProcessingCheckResult:
    """Result of processing status check."""
    status: ProcessingStatus
    should_process: bool
    reason: str
    cached_record: Optional[ProcessedTicket] = None
    jira_attachment: Optional[JiraAttachment] = None
    error: Optional[str] = None


def is_ticket_already_processed(config: Config, 
                              ticket_key: str,
                              issue_data: Optional[Dict[str, Any]] = None,
                              check_jira: bool = True,
                              force_jira_check: bool = False) -> ProcessingCheckResult:
    """Check if a ticket has already been processed.
    
    This function implements the core duplicate detection logic:
    1. Check local database cache first (fast lookup)
    2. If not cached or force_jira_check=True, check Jira for our attachment
    3. Update cache with Jira result
    4. Return processing recommendation
    
    Args:
        config: Jira configuration
        ticket_key: Jira ticket key (e.g., 'PROJ-123')
        issue_data: Optional pre-fetched Jira issue data (to avoid extra API calls)
        check_jira: Whether to check Jira for attachments (default True)
        force_jira_check: Force check Jira even if we have cache hit (default False)
        
    Returns:
        ProcessingCheckResult with recommendation and metadata
    """
    db = get_processed_tickets_db()
    
    debug_log(
        run_id="debug",
        hypothesis_id="H1",
        location="jira_triage/duplicate_detection.py:should_skip_processing",
        message="duplicate_detection_start",
        data={
            "ticket_key": ticket_key,
            "check_jira": check_jira,
            "force_jira_check": force_jira_check,
            "has_issue_data": issue_data is not None
        }
    )
    
    # Step 1: Check local database cache
    cached_record = db.get_processed_ticket(ticket_key)
    
    if cached_record and not force_jira_check:
        debug_log(
            run_id="debug",
            hypothesis_id="H1",
            location="jira_triage/duplicate_detection.py:should_skip_processing",
            message="duplicate_detection_cache_hit",
            data={
                "ticket_key": ticket_key,
                "processed_at": cached_record.processed_at.isoformat(),
                "processing_mode": cached_record.processing_mode,
                "jira_attachment_id": cached_record.jira_attachment_id
            }
        )
        
        return ProcessingCheckResult(
            status=ProcessingStatus.CACHE_HIT,
            should_process=False,
            reason=f"Found in local cache, processed on {cached_record.processed_at.strftime('%Y-%m-%d %H:%M')} via {cached_record.processing_mode}",
            cached_record=cached_record
        )
    
    # Step 2: Check Jira for our attachment (if enabled and we have issue data)
    if not check_jira or not issue_data:
        # #region agent log
        try:
            import json as _json, time as _time
            _log_payload = {
                "sessionId": "87c699",
                "runId": "debug_run_1",
                "hypothesisId": "1,2",
                "location": "jira_triage/duplicate_detection.py:103",
                "message": "Calling debug_log with positional args in duplicate_detection",
                "data": {
                    "ticket_key": ticket_key
                },
                "timestamp": int(_time.time() * 1000)
            }
            with open("/Users/abijithp/Desktop/Jira-triage/.cursor/debug-87c699.log", "a", encoding="utf-8") as _f:
                _f.write(_json.dumps(_log_payload) + "\n")
        except Exception: pass
        # #endregion

        try:
            debug_log("duplicate_detection_skip_jira", {
                "ticket_key": ticket_key,
                "check_jira": check_jira,
                "has_issue_data": issue_data is not None,
                "reason": "Jira check disabled or no issue data provided"
            })
        except Exception as e:
            # #region agent log
            try:
                import json as _json, time as _time
                _log_payload = {
                    "sessionId": "87c699",
                    "runId": "debug_run_1",
                    "hypothesisId": "1,2",
                    "location": "jira_triage/duplicate_detection.py:128",
                    "message": "Caught error in debug_log call in duplicate_detection",
                    "data": {
                        "error": str(e),
                        "error_type": type(e).__name__
                    },
                    "timestamp": int(_time.time() * 1000)
                }
                with open("/Users/abijithp/Desktop/Jira-triage/.cursor/debug-87c699.log", "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps(_log_payload) + "\n")
            except Exception: pass
            # #endregion
            raise
        
        return ProcessingCheckResult(
            status=ProcessingStatus.NOT_PROCESSED,
            should_process=True,
            reason="No cached record and Jira check not performed",
            cached_record=cached_record
        )
    
    try:
        # Check if our attachment exists in Jira
        attachment_exists = check_our_attachment_exists(issue_data)
        
        if attachment_exists:
            # Get attachment info for metadata
            attachment_info = get_our_attachment_info(issue_data)
            
            # Update local cache with Jira findings
            if attachment_info:
                db.mark_ticket_processed(
                    ticket_key=ticket_key,
                    processing_mode="detected",  # We detected it was already processed
                    jira_attachment_id=attachment_info.id,
                    attachment_filename=attachment_info.filename,
                    metadata={
                        "detected_via": "attachment_scan",
                        "attachment_created": attachment_info.created.isoformat(),
                        "attachment_author": attachment_info.author_display_name
                    }
                )
            
            debug_log("duplicate_detection_jira_found", {
                "ticket_key": ticket_key,
                "attachment_id": attachment_info.id if attachment_info else None,
                "attachment_filename": attachment_info.filename if attachment_info else None
            })
            
            return ProcessingCheckResult(
                status=ProcessingStatus.PROCESSED_BY_US,
                should_process=False,
                reason=f"Our analysis bundle found in Jira: {attachment_info.filename if attachment_info else 'unknown file'}",
                cached_record=cached_record,
                jira_attachment=attachment_info
            )
        
        else:
            # No attachment found - check if we had a stale cache entry
            if cached_record:
                debug_log("duplicate_detection_stale_cache", {
                    "ticket_key": ticket_key,
                    "cached_attachment_id": cached_record.jira_attachment_id,
                    "reason": "Cache indicated processed but no Jira attachment found"
                })
                
                # Remove stale cache entry
                db.remove_ticket(ticket_key)
                
                return ProcessingCheckResult(
                    status=ProcessingStatus.ATTACHMENT_MISSING,
                    should_process=True,
                    reason="Cached as processed but Jira attachment not found (may have been manually deleted)",
                    cached_record=cached_record
                )
            
            debug_log("duplicate_detection_not_processed", {
                "ticket_key": ticket_key,
                "reason": "No attachment found, ticket needs processing"
            })
            
            return ProcessingCheckResult(
                status=ProcessingStatus.NOT_PROCESSED,
                should_process=True,
                reason="No analysis bundle found in Jira",
                cached_record=cached_record
            )
    
    except Exception as e:
        debug_log("duplicate_detection_error", {
            "ticket_key": ticket_key,
            "error": str(e),
            "error_type": type(e).__name__
        })
        
        # On error, err on the side of processing (better to duplicate than miss)
        return ProcessingCheckResult(
            status=ProcessingStatus.ERROR,
            should_process=True,
            reason=f"Error during duplicate detection: {str(e)}",
            cached_record=cached_record,
            error=str(e)
        )


def should_skip_processing(config: Config,
                          ticket_key: str,
                          processing_mode: str,
                          issue_data: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Determine if ticket processing should be skipped.
    
    This is the main entry point for duplicate detection that considers
    the processing mode and configuration settings.
    
    Args:
        config: Jira configuration
        ticket_key: Jira ticket key
        processing_mode: How ticket is being processed ('webhook', 'polling', 'manual')
        issue_data: Optional pre-fetched Jira issue data
        
    Returns:
        Tuple of (should_skip: bool, reason: str)
    """
    # CLI/manual mode always processes (manual override)
    if processing_mode == "manual":
        debug_log("skip_processing_manual_override", {
            "ticket_key": ticket_key,
            "processing_mode": processing_mode
        })
        return False, "Manual processing mode - always process"
    
    # Check if duplicate detection is disabled
    check_attachments = getattr(config, 'jira_check_our_attachments', True)
    if not check_attachments:
        debug_log("skip_processing_disabled", {
            "ticket_key": ticket_key,
            "reason": "Attachment-based duplicate detection disabled"
        })
        return False, "Duplicate detection disabled via configuration"
    
    # Webhook-specific settings
    if processing_mode == "webhook":
        webhook_skip_duplicates = getattr(config, 'webhook_skip_duplicates', True)
        if not webhook_skip_duplicates:
            debug_log("skip_processing_webhook_force", {
                "ticket_key": ticket_key,
                "reason": "Webhook configured to force reprocess"
            })
            return False, "Webhook mode configured to always process"
    
    # Perform the duplicate detection
    result = is_ticket_already_processed(
        config=config,
        ticket_key=ticket_key,
        issue_data=issue_data,
        check_jira=True,
        force_jira_check=False
    )
    
    should_skip = not result.should_process
    
    debug_log("skip_processing_decision", {
        "ticket_key": ticket_key,
        "processing_mode": processing_mode,
        "should_skip": should_skip,
        "status": result.status.value,
        "reason": result.reason
    })
    
    return should_skip, result.reason


def mark_processing_complete(config: Config,
                           ticket_key: str,
                           processing_mode: str,
                           jira_attachment_id: Optional[str] = None,
                           attachment_filename: Optional[str] = None,
                           analysis_content: Optional[str] = None) -> None:
    """Mark a ticket as successfully processed.
    
    Args:
        config: Jira configuration
        ticket_key: Jira ticket key
        processing_mode: How ticket was processed
        jira_attachment_id: ID of uploaded attachment
        attachment_filename: Name of uploaded attachment
        analysis_content: Analysis content for change detection
    """
    db = get_processed_tickets_db()
    
    db.mark_ticket_processed(
        ticket_key=ticket_key,
        processing_mode=processing_mode,
        jira_attachment_id=jira_attachment_id,
        attachment_filename=attachment_filename,
        analysis_content=analysis_content,
        metadata={
            "completed_via": "mark_processing_complete",
            "has_attachment": jira_attachment_id is not None
        }
    )
    
    debug_log("processing_marked_complete", {
        "ticket_key": ticket_key,
        "processing_mode": processing_mode,
        "jira_attachment_id": jira_attachment_id,
        "attachment_filename": attachment_filename
    })


def get_processing_stats() -> Dict[str, Any]:
    """Get statistics about processed tickets.
    
    Returns:
        Dictionary with processing statistics
    """
    db = get_processed_tickets_db()
    return db.get_stats()