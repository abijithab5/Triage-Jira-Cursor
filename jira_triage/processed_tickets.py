"""
Persistent tracking of processed Jira tickets using SQLite database.

This module provides the database layer for tracking which tickets have been
analyzed, including metadata about our uploaded attachments. This ensures
duplicate detection survives reboots and directory cleanup.
"""

import json
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any
from dataclasses import dataclass

from .config import load_config
from .debug_log import debug_log


@dataclass
class ProcessedTicket:
    """Represents a processed ticket record."""
    ticket_key: str
    processed_at: datetime
    jira_attachment_id: Optional[str]
    attachment_filename: Optional[str]
    analysis_hash: Optional[str]
    processing_mode: str
    metadata: Optional[Dict[str, Any]] = None


class ProcessedTicketsDB:
    """SQLite database for tracking processed Jira tickets."""
    
    def __init__(self, db_path: Optional[Path] = None):
        """Initialize the database connection.
        
        Args:
            db_path: Path to SQLite database file. If None, uses default location.
        """
        if db_path is None:
            config = load_config()
            # Store in project data directory
            db_path = Path.cwd() / "data" / "processed_tickets.db"
        
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize database schema
        self._init_schema()
        
        # #region agent log
        try:
            import json as _json, time as _time
            _log_payload = {
                "sessionId": "87c699",
                "runId": "debug_run_1",
                "hypothesisId": "1,2",
                "location": "jira_triage/processed_tickets.py:53",
                "message": "Calling debug_log with positional args",
                "data": {
                    "db_path": str(self.db_path),
                    "debug_log_type": str(type(debug_log)),
                    "debug_log_name": getattr(debug_log, "__name__", "unknown")
                },
                "timestamp": int(_time.time() * 1000)
            }
            with open("/Users/abijithp/Desktop/Jira-triage/.cursor/debug-87c699.log", "a", encoding="utf-8") as _f:
                _f.write(_json.dumps(_log_payload) + "\n")
        except Exception: pass
        # #endregion

        try:
            debug_log("processed_tickets_db_init", {
                "db_path": str(self.db_path),
                "db_exists": self.db_path.exists()
            })
        except Exception as e:
            # #region agent log
            try:
                import json as _json, time as _time
                _log_payload = {
                    "sessionId": "87c699",
                    "runId": "debug_run_1",
                    "hypothesisId": "1,2",
                    "location": "jira_triage/processed_tickets.py:65",
                    "message": "Caught error in debug_log call",
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
    
    def _init_schema(self) -> None:
        """Initialize database schema if it doesn't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_tickets (
                    ticket_key TEXT PRIMARY KEY,
                    processed_at TIMESTAMP NOT NULL,
                    jira_attachment_id TEXT,
                    attachment_filename TEXT,
                    analysis_hash TEXT,
                    processing_mode TEXT NOT NULL,
                    metadata_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create index for performance
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_processed_at 
                ON processed_tickets(processed_at DESC)
            """)
            
            # Create trigger to update updated_at
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS update_processed_tickets_updated_at
                AFTER UPDATE ON processed_tickets
                FOR EACH ROW
                BEGIN
                    UPDATE processed_tickets 
                    SET updated_at = CURRENT_TIMESTAMP 
                    WHERE ticket_key = NEW.ticket_key;
                END
            """)
            
            conn.commit()
    
    def is_ticket_processed(self, ticket_key: str) -> bool:
        """Check if a ticket has been processed.
        
        Args:
            ticket_key: Jira ticket key (e.g., 'PROJ-123')
            
        Returns:
            True if ticket has been processed, False otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT 1 FROM processed_tickets WHERE ticket_key = ? LIMIT 1",
                (ticket_key,)
            )
            result = cursor.fetchone() is not None
            
        debug_log("ticket_processed_check", {
            "ticket_key": ticket_key,
            "is_processed": result
        })
        
        return result
    
    def get_processed_ticket(self, ticket_key: str) -> Optional[ProcessedTicket]:
        """Get processed ticket record.
        
        Args:
            ticket_key: Jira ticket key
            
        Returns:
            ProcessedTicket instance or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT ticket_key, processed_at, jira_attachment_id,
                       attachment_filename, analysis_hash, processing_mode,
                       metadata_json
                FROM processed_tickets 
                WHERE ticket_key = ?
            """, (ticket_key,))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            # Parse datetime and metadata
            processed_at = datetime.fromisoformat(row['processed_at'].replace('Z', '+00:00'))
            metadata = None
            if row['metadata_json']:
                try:
                    metadata = json.loads(row['metadata_json'])
                except json.JSONDecodeError:
                    debug_log("metadata_parse_error", {
                        "ticket_key": ticket_key,
                        "metadata_json": row['metadata_json']
                    })
            
            return ProcessedTicket(
                ticket_key=row['ticket_key'],
                processed_at=processed_at,
                jira_attachment_id=row['jira_attachment_id'],
                attachment_filename=row['attachment_filename'],
                analysis_hash=row['analysis_hash'],
                processing_mode=row['processing_mode'],
                metadata=metadata
            )
    
    def mark_ticket_processed(self, 
                            ticket_key: str,
                            processing_mode: str,
                            jira_attachment_id: Optional[str] = None,
                            attachment_filename: Optional[str] = None,
                            analysis_content: Optional[str] = None,
                            metadata: Optional[Dict[str, Any]] = None) -> None:
        """Mark a ticket as processed.
        
        Args:
            ticket_key: Jira ticket key
            processing_mode: How ticket was processed ('webhook', 'polling', 'manual')
            jira_attachment_id: ID of attachment uploaded to Jira
            attachment_filename: Filename of uploaded attachment
            analysis_content: Content to hash for change detection
            metadata: Additional metadata to store
        """
        # Generate content hash if provided
        analysis_hash = None
        if analysis_content:
            analysis_hash = hashlib.sha256(analysis_content.encode('utf-8')).hexdigest()[:16]
        
        # Serialize metadata
        metadata_json = None
        if metadata:
            metadata_json = json.dumps(metadata, ensure_ascii=False)
        
        processed_at = datetime.now(timezone.utc).isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO processed_tickets 
                (ticket_key, processed_at, jira_attachment_id, attachment_filename,
                 analysis_hash, processing_mode, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                ticket_key, processed_at, jira_attachment_id,
                attachment_filename, analysis_hash, processing_mode, metadata_json
            ))
            conn.commit()
        
        debug_log("ticket_marked_processed", {
            "ticket_key": ticket_key,
            "processing_mode": processing_mode,
            "jira_attachment_id": jira_attachment_id,
            "attachment_filename": attachment_filename,
            "analysis_hash": analysis_hash
        })
    
    def get_recent_tickets(self, limit: int = 100) -> List[ProcessedTicket]:
        """Get recently processed tickets.
        
        Args:
            limit: Maximum number of tickets to return
            
        Returns:
            List of ProcessedTicket instances, most recent first
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT ticket_key, processed_at, jira_attachment_id,
                       attachment_filename, analysis_hash, processing_mode,
                       metadata_json
                FROM processed_tickets 
                ORDER BY processed_at DESC
                LIMIT ?
            """, (limit,))
            
            tickets = []
            for row in cursor.fetchall():
                processed_at = datetime.fromisoformat(row['processed_at'].replace('Z', '+00:00'))
                metadata = None
                if row['metadata_json']:
                    try:
                        metadata = json.loads(row['metadata_json'])
                    except json.JSONDecodeError:
                        pass
                
                tickets.append(ProcessedTicket(
                    ticket_key=row['ticket_key'],
                    processed_at=processed_at,
                    jira_attachment_id=row['jira_attachment_id'],
                    attachment_filename=row['attachment_filename'],
                    analysis_hash=row['analysis_hash'],
                    processing_mode=row['processing_mode'],
                    metadata=metadata
                ))
            
            return tickets
    
    def remove_ticket(self, ticket_key: str) -> bool:
        """Remove a ticket from the processed records.
        
        Args:
            ticket_key: Jira ticket key
            
        Returns:
            True if ticket was removed, False if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM processed_tickets WHERE ticket_key = ?",
                (ticket_key,)
            )
            removed = cursor.rowcount > 0
            conn.commit()
        
        debug_log("ticket_removed", {
            "ticket_key": ticket_key,
            "was_removed": removed
        })
        
        return removed
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics.
        
        Returns:
            Dictionary with database statistics
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM processed_tickets")
            total_count = cursor.fetchone()[0]
            
            cursor = conn.execute("""
                SELECT processing_mode, COUNT(*) as count
                FROM processed_tickets 
                GROUP BY processing_mode
            """)
            mode_counts = dict(cursor.fetchall())
            
            cursor = conn.execute("""
                SELECT COUNT(*) FROM processed_tickets 
                WHERE jira_attachment_id IS NOT NULL
            """)
            with_attachments = cursor.fetchone()[0]
            
            cursor = conn.execute("""
                SELECT MAX(processed_at) FROM processed_tickets
            """)
            last_processed = cursor.fetchone()[0]
        
        return {
            "total_tickets": total_count,
            "by_mode": mode_counts,
            "with_attachments": with_attachments,
            "last_processed": last_processed,
            "db_path": str(self.db_path),
            "db_size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0
        }


# Global instance for easy access
_db_instance: Optional[ProcessedTicketsDB] = None


def get_processed_tickets_db(db_path: Optional[Path] = None) -> ProcessedTicketsDB:
    """Get the global ProcessedTicketsDB instance.
    
    Args:
        db_path: Optional path to database file
        
    Returns:
        ProcessedTicketsDB instance
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = ProcessedTicketsDB(db_path)
    return _db_instance


def is_ticket_processed(ticket_key: str) -> bool:
    """Convenience function to check if ticket is processed.
    
    Args:
        ticket_key: Jira ticket key
        
    Returns:
        True if processed, False otherwise
    """
    db = get_processed_tickets_db()
    return db.is_ticket_processed(ticket_key)


def mark_ticket_processed(ticket_key: str, 
                         processing_mode: str,
                         **kwargs) -> None:
    """Convenience function to mark ticket as processed.
    
    Args:
        ticket_key: Jira ticket key
        processing_mode: Processing mode ('webhook', 'polling', 'manual')
        **kwargs: Additional arguments passed to mark_ticket_processed
    """
    db = get_processed_tickets_db()
    db.mark_ticket_processed(ticket_key, processing_mode, **kwargs)