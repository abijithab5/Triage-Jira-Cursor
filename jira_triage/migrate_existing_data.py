#!/usr/bin/env python3
"""
Migration script to backfill processed tickets database from existing output directories.

This script scans existing output directories and populates the processed_tickets database
with information about previously processed tickets. This ensures that the new attachment-based
duplicate detection system is aware of tickets that were processed before the upgrade.
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from .config import load_config
from .debug_log import debug_log
from .processed_tickets import get_processed_tickets_db


def scan_output_directories(output_base_path: Path) -> List[Dict[str, Any]]:
    """Scan output directories for previously processed tickets.
    
    Args:
        output_base_path: Base path containing ticket directories
        
    Returns:
        List of ticket information dictionaries
    """
    if not output_base_path.exists():
        print(f"Output directory does not exist: {output_base_path}")
        return []
    
    tickets_found = []
    
    print(f"Scanning output directories in: {output_base_path}")
    
    for ticket_dir in output_base_path.iterdir():
        if not ticket_dir.is_dir():
            continue
            
        ticket_key = ticket_dir.name
        
        # Check for key indicator files that show processing was completed
        issue_json_path = ticket_dir / "issue.json"
        context_md_path = ticket_dir / "context.md"
        analysis_path = ticket_dir / "analysis.md"
        upload_result_path = ticket_dir / "jira_attachment_upload.json"
        
        if not issue_json_path.exists():
            print(f"  Skipping {ticket_key}: no issue.json found")
            continue
            
        try:
            # Extract ticket information
            ticket_info = {
                "ticket_key": ticket_key,
                "ticket_dir": ticket_dir,
                "has_issue_json": issue_json_path.exists(),
                "has_context_md": context_md_path.exists(),
                "has_analysis": analysis_path.exists(),
                "has_upload_result": upload_result_path.exists(),
                "processed_at": None,
                "jira_attachment_id": None,
                "attachment_filename": None,
                "analysis_content": None,
                "processing_mode": "migrated",  # Mark as migrated
            }
            
            # Try to extract processing timestamp from various sources
            processed_at = None
            
            # 1. From upload result file (most reliable)
            if upload_result_path.exists():
                try:
                    upload_data = json.loads(upload_result_path.read_text(encoding="utf-8"))
                    if upload_data.get("ok") and upload_data.get("response"):
                        # Try to extract attachment ID
                        response = upload_data.get("response", [])
                        if isinstance(response, list) and len(response) > 0:
                            ticket_info["jira_attachment_id"] = str(response[0].get("id", ""))
                        elif isinstance(response, dict):
                            ticket_info["jira_attachment_id"] = str(response.get("id", ""))
                        
                        # Try to extract filename
                        ticket_info["attachment_filename"] = upload_data.get("attachment_filename")
                    
                    # Use file modification time as processing time
                    processed_at = datetime.fromtimestamp(upload_result_path.stat().st_mtime, tz=timezone.utc)
                except Exception as e:
                    print(f"    Warning: Error reading upload result for {ticket_key}: {e}")
            
            # 2. From context.md file modification time
            if not processed_at and context_md_path.exists():
                processed_at = datetime.fromtimestamp(context_md_path.stat().st_mtime, tz=timezone.utc)
            
            # 3. From issue.json file modification time (fallback)
            if not processed_at and issue_json_path.exists():
                processed_at = datetime.fromtimestamp(issue_json_path.stat().st_mtime, tz=timezone.utc)
            
            # 4. Default to epoch if nothing found
            if not processed_at:
                processed_at = datetime.fromtimestamp(0, tz=timezone.utc)
            
            ticket_info["processed_at"] = processed_at
            
            # Try to read analysis content for change detection
            if analysis_path.exists():
                try:
                    ticket_info["analysis_content"] = analysis_path.read_text(encoding="utf-8", errors="ignore")[:1000]  # First 1KB
                except Exception:
                    pass
            
            tickets_found.append(ticket_info)
            print(f"  ✓ Found ticket: {ticket_key} (processed: {processed_at.strftime('%Y-%m-%d %H:%M')})")
            
        except Exception as e:
            print(f"  ✗ Error processing {ticket_key}: {e}")
            continue
    
    return tickets_found


def backfill_database(tickets: List[Dict[str, Any]], dry_run: bool = False) -> Dict[str, int]:
    """Backfill the processed tickets database with discovered tickets.
    
    Args:
        tickets: List of ticket information dictionaries
        dry_run: If True, only show what would be done without making changes
        
    Returns:
        Dictionary with statistics about the migration
    """
    db = get_processed_tickets_db()
    
    stats = {
        "total_found": len(tickets),
        "already_exists": 0,
        "added": 0,
        "errors": 0,
    }
    
    print(f"\nBackfilling database with {len(tickets)} tickets (dry_run={dry_run})")
    
    for ticket_info in tickets:
        ticket_key = ticket_info["ticket_key"]
        
        try:
            # Check if already exists
            if db.is_ticket_processed(ticket_key):
                print(f"  - {ticket_key}: Already in database, skipping")
                stats["already_exists"] += 1
                continue
            
            if dry_run:
                print(f"  + {ticket_key}: Would add to database")
                stats["added"] += 1
                continue
            
            # Add to database
            db.mark_ticket_processed(
                ticket_key=ticket_key,
                processing_mode=ticket_info["processing_mode"],
                jira_attachment_id=ticket_info.get("jira_attachment_id"),
                attachment_filename=ticket_info.get("attachment_filename"),
                analysis_content=ticket_info.get("analysis_content"),
                metadata={
                    "migrated_from": "output_directory",
                    "migration_timestamp": datetime.now(timezone.utc).isoformat(),
                    "original_processed_at": ticket_info["processed_at"].isoformat(),
                    "has_context_md": ticket_info["has_context_md"],
                    "has_analysis": ticket_info["has_analysis"],
                    "has_upload_result": ticket_info["has_upload_result"],
                }
            )
            
            print(f"  ✓ {ticket_key}: Added to database")
            stats["added"] += 1
            
        except Exception as e:
            print(f"  ✗ {ticket_key}: Error adding to database: {e}")
            stats["errors"] += 1
            
            debug_log("migration_error", {
                "ticket_key": ticket_key,
                "error": str(e),
                "error_type": type(e).__name__
            })
    
    return stats


def main():
    """Main migration function."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Migrate existing ticket output directories to processed tickets database"
    )
    parser.add_argument(
        "--dry-run", 
        action="store_true", 
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--output-dir", 
        type=Path, 
        help="Output directory path (default: from config)"
    )
    parser.add_argument(
        "--force", 
        action="store_true",
        help="Process all tickets even if they exist in database"
    )
    
    args = parser.parse_args()
    
    try:
        # Load configuration
        config = load_config()
        
        # Determine output directory
        if args.output_dir:
            output_base_path = args.output_dir
        else:
            output_base_path = config.output_dir
            if not output_base_path.is_absolute():
                # Try to resolve relative to current directory
                output_base_path = Path.cwd() / output_base_path
        
        output_base_path = output_base_path.resolve()
        
        print("=== Jira Triage Database Migration ===")
        print(f"Output directory: {output_base_path}")
        print(f"Database path: {config.processed_tickets_db}")
        print(f"Dry run: {args.dry_run}")
        print()
        
        # Scan for existing tickets
        tickets = scan_output_directories(output_base_path)
        
        if not tickets:
            print("No ticket directories found to migrate.")
            return 0
        
        # Backfill database
        stats = backfill_database(tickets, dry_run=args.dry_run)
        
        # Print summary
        print("\n=== Migration Summary ===")
        print(f"Total tickets found: {stats['total_found']}")
        print(f"Already in database: {stats['already_exists']}")
        print(f"Added to database: {stats['added']}")
        print(f"Errors: {stats['errors']}")
        
        if args.dry_run:
            print("\nThis was a dry run. Use --dry-run=false to actually perform the migration.")
        elif stats['added'] > 0:
            print(f"\nSuccessfully migrated {stats['added']} tickets to the database.")
            print("The new attachment-based duplicate detection system is now aware of these tickets.")
        
        # Show database stats
        if not args.dry_run:
            print("\n=== Database Statistics ===")
            db = get_processed_tickets_db()
            db_stats = db.get_stats()
            print(f"Total tickets in database: {db_stats['total_tickets']}")
            for mode, count in db_stats['by_mode'].items():
                print(f"  {mode}: {count}")
        
        return 0 if stats['errors'] == 0 else 1
        
    except Exception as e:
        print(f"Migration failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())