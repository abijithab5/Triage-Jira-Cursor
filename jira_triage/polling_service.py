"""Continuous Jira ticket polling service."""

from __future__ import annotations

import signal
import sys
import time
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .core import TriageError, normalize_ticket_key, triage
from .duplicate_detection import should_skip_processing, mark_processing_complete
from .jira_client import JiraError, search_issues
from .logging_config import setup_polling_logging


class PollingService:
    """Continuous Jira ticket polling service."""
    
    def __init__(self, config: Config | None = None) -> None:
        """Initialize the polling service."""
        self.config = config or load_config()
        self.logger = setup_polling_logging()
        self.running = False
        self.processed_tickets: set[str] = set()
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle shutdown signals gracefully."""
        signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        self.logger.info("Received %s, shutting down polling service gracefully...", signal_name)
        self.stop()
    
    def should_skip_ticket(self, ticket_key: str, issue_data: dict[str, Any]) -> tuple[bool, str]:
        """Check if ticket should be skipped using attachment-based detection."""
        # Check if already processed in this session (fast check)
        if ticket_key in self.processed_tickets:
            return True, "Already processed in this polling session"
        
        # Use unified duplicate detection with issue data
        return should_skip_processing(
            config=self.config,
            ticket_key=ticket_key,
            processing_mode="polling",
            issue_data=issue_data
        )
    
    def get_tickets_to_process(self) -> list[dict[str, Any]]:
        """Get list of tickets that need processing using JQL query."""
        try:
            # Use configured JQL or default to assigned tickets
            jql = getattr(self.config, 'polling_jql', 'assignee = currentUser() ORDER BY updated DESC')
            max_results = getattr(self.config, 'polling_max_results', 50)
            
            self.logger.debug("Executing JQL query: %s (max_results=%d)", jql, max_results)
            
            # Search for tickets
            issues = search_issues(self.config, jql, max_results, fields="key,updated,assignee,summary,status,attachment")
            
            self.logger.info("JQL search returned %d issues", len(issues))
            
            # Filter out already processed tickets using attachment-based detection
            new_tickets = []
            skipped_tickets = []
            
            for issue in issues:
                ticket_key = issue.get("key", "")
                if not ticket_key:
                    continue
                
                should_skip, reason = self.should_skip_ticket(ticket_key, issue)
                if should_skip:
                    self.logger.debug("Skipping ticket %s: %s", ticket_key, reason)
                    skipped_tickets.append(ticket_key)
                    continue
                
                new_tickets.append(issue)
            
            if skipped_tickets:
                self.logger.info("Skipped %d already processed tickets: %s", 
                               len(skipped_tickets), skipped_tickets)
            
            self.logger.info("Found %d new tickets to process: %s", 
                           len(new_tickets), [t.get("key") for t in new_tickets])
            
            return new_tickets
            
        except JiraError as e:
            self.logger.error("Failed to search for tickets: %s", e)
            return []
        except Exception as e:
            self.logger.error("Unexpected error during ticket search: %s", e, exc_info=True)
            return []
    
    def process_ticket(self, issue: dict[str, Any], dry_run: bool = False) -> bool:
        """Process a single ticket using the triage workflow."""
        ticket_key = issue.get("key", "")
        if not ticket_key:
            self.logger.warning("Issue missing key field: %s", issue)
            return False
        
        try:
            # Normalize ticket key
            normalized_key = normalize_ticket_key(ticket_key)
            
            if dry_run:
                self.logger.info("DRY RUN: Would process ticket %s - %s", 
                               normalized_key, issue.get("fields", {}).get("summary", "No summary"))
                return True
            
            self.logger.info("Processing ticket: %s - %s", 
                           normalized_key, issue.get("fields", {}).get("summary", "No summary"))
            
            # Call triage with attachment upload enabled for consistent behavior
            result = triage(
                normalized_key,
                mode="polling",  # Use polling mode for proper tracking
                open_cursor=False,  # Don't auto-open in polling mode
                process_logs=False,  # Consistent with webhook: no log processing
                cursor_analysis=True,  # Enable full Cursor analysis
                attach=True,  # CHANGED: Enable attachment upload for duplicate detection
                repo=None,  # Use default repo detection
                logs_dir=None,  # Use default logs configuration
            )
            
            # Track processed ticket in session cache
            self.processed_tickets.add(normalized_key)
            
            # Mark as processed in persistent database
            # Note: The triage function will call mark_processing_complete internally
            # when attach=True and upload succeeds
            
            self.logger.info("Successfully processed ticket %s - output at: %s", 
                           normalized_key, result.output_dir)
            
            return True
            
        except TriageError as e:
            self.logger.error("Failed to process ticket %s: %s", ticket_key, e)
            return False
        except Exception as e:
            self.logger.error("Unexpected error processing ticket %s: %s", ticket_key, e, exc_info=True)
            return False
    
    def run_single_poll(self, dry_run: bool = False) -> dict[str, int]:
        """Run a single polling cycle and return statistics."""
        self.logger.debug("Starting polling cycle (dry_run=%s)", dry_run)
        
        stats = {
            "tickets_found": 0,
            "tickets_processed": 0,
            "tickets_skipped": 0,
            "tickets_failed": 0,
        }
        
        # Get tickets to process
        tickets = self.get_tickets_to_process()
        stats["tickets_found"] = len(tickets)
        
        if not tickets:
            self.logger.debug("No new tickets found")
            return stats
        
        # Process each ticket
        for issue in tickets:
            ticket_key = issue.get("key", "unknown")
            
            if self.process_ticket(issue, dry_run):
                stats["tickets_processed"] += 1
                self.logger.info("✓ Processed ticket: %s", ticket_key)
            else:
                stats["tickets_failed"] += 1
                self.logger.warning("✗ Failed to process ticket: %s", ticket_key)
        
        poll_summary = (
            f"Poll cycle complete: {stats['tickets_found']} found, "
            f"{stats['tickets_processed']} processed, "
            f"{stats['tickets_failed']} failed"
        )
        
        if dry_run:
            poll_summary += " (DRY RUN)"
            
        self.logger.info(poll_summary)
        
        return stats
    
    def start(self, interval_seconds: int | None = None, dry_run: bool = False) -> None:
        """Start the continuous polling service."""
        if interval_seconds is None:
            interval_seconds = getattr(self.config, 'polling_interval_seconds', 300)  # 5 minutes default
        
        self.logger.info("Starting Jira polling service")
        self.logger.info("  Polling interval: %d seconds (%d minutes)", interval_seconds, interval_seconds // 60)
        self.logger.info("  Dry run mode: %s", dry_run)
        self.logger.info("  Jira base URL: %s", self.config.jira_base_url)
        
        if dry_run:
            self.logger.info("  DRY RUN MODE: No tickets will be actually processed")
        
        self.running = True
        
        try:
            while self.running:
                cycle_start = time.time()
                
                # Run polling cycle
                stats = self.run_single_poll(dry_run)
                
                cycle_duration = time.time() - cycle_start
                
                if stats["tickets_found"] > 0:
                    self.logger.info("Processed %d/%d tickets in %.1fs", 
                                   stats["tickets_processed"], 
                                   stats["tickets_found"], 
                                   cycle_duration)
                
                # Wait for next poll interval
                if self.running:  # Check if we haven't been stopped during processing
                    self.logger.debug("Waiting %d seconds until next poll...", interval_seconds)
                    time.sleep(interval_seconds)
                    
        except KeyboardInterrupt:
            self.logger.info("Received keyboard interrupt, shutting down...")
        except Exception as e:
            self.logger.error("Fatal error in polling loop: %s", e, exc_info=True)
            raise
        finally:
            self.logger.info("Polling service stopped")
    
    def stop(self) -> None:
        """Stop the polling service."""
        self.running = False
    
    def run_once(self, dry_run: bool = False) -> dict[str, int]:
        """Run a single poll cycle and exit."""
        self.logger.info("Running single poll cycle (dry_run=%s)", dry_run)
        stats = self.run_single_poll(dry_run)
        self.logger.info("Single poll cycle completed")
        return stats


def create_polling_service(config: Config | None = None) -> PollingService:
    """Create and return a configured polling service instance."""
    return PollingService(config)


def run_polling_daemon(interval_seconds: int = 300, dry_run: bool = False) -> None:
    """Run the polling service as a daemon process."""
    service = create_polling_service()
    service.start(interval_seconds, dry_run)


def run_polling_once(dry_run: bool = False) -> dict[str, int]:
    """Run a single polling cycle and return statistics."""
    service = create_polling_service()
    return service.run_once(dry_run)