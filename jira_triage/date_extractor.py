"""Extract and parse dates from Jira ticket descriptions."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from .debug_log import debug_log


class DateExtractionError(RuntimeError):
    pass


def extract_dates_from_description(description: str | None) -> tuple[datetime, datetime]:
    """
    Extract start and end dates from Jira ticket description.
    
    Supports multiple date formats:
    - ISO 8601: 2026-05-08, 2026-05-08T15:30:00
    - European dotted: 07.05.2026 (DD.MM.YYYY, common in DE templates)
    - Named: May 8, 2026; 8 May 2026; May 8
    - Mixed patterns: "from May 8 to May 10", "between 2026-05-04 and 2026-05-08"
    - Relative: "today", "yesterday", "last 7 days", "last 24 hours"
    
    Returns: (start_date, end_date) as UTC datetimes with time 00:00:00
    Fallback: Returns last 7 days if no dates found
    """
    
    if not description or not str(description).strip():
        return _default_date_range()
    
    desc = str(description).strip()
    
    # Try to extract date patterns from description
    dates = _find_dates_in_text(desc)
    
    if dates:
        debug_log(
            run_id="magnus-log",
            hypothesis_id="date-extraction",
            location="jira_triage/date_extractor.py:extract_dates_from_description",
            message="Extracted dates from description",
            data={
                "found_dates_count": len(dates),
                "dates": [d.isoformat() for d in dates],
            },
        )
        
        # Sort dates and take min/max
        dates_sorted = sorted(dates)
        start_date = dates_sorted[0]
        end_date = dates_sorted[-1]
        
        # Add 1 day before start for broader analysis
        start_date = start_date - timedelta(days=1)
        
        return (start_date, end_date)
    
    # Fallback to default range
    return _default_date_range()


def _default_date_range() -> tuple[datetime, datetime]:
    """Return last 7 days as default range."""
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = now - timedelta(days=7)
    return (start, now)


def _find_dates_in_text(text: str) -> list[datetime]:
    """Find all date patterns in text."""
    dates: list[datetime] = []
    
    # European dotted: DD.MM.YYYY or D.M.YYYY (common in DE Jira templates, e.g. "07.05.2026 1:15 am")
    eu_dot_pattern = r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b"
    for match in re.finditer(eu_dot_pattern, text):
        try:
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            dt = datetime(year, month, day, tzinfo=timezone.utc)
            dates.append(dt)
        except (ValueError, TypeError):
            continue

    # ISO 8601 format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS
    iso_pattern = r'\b(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2}):(\d{2}))?\b'
    for match in re.finditer(iso_pattern, text):
        try:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            hour = int(match.group(4) or 0)
            minute = int(match.group(5) or 0)
            second = int(match.group(6) or 0)
            
            dt = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
            dates.append(dt)
        except (ValueError, TypeError):
            continue
    
    # Named formats: "May 8, 2026", "8 May 2026", "May 8"
    month_pattern = r'\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+(\d{1,2})(?:,?\s+(\d{4}))?\b'
    for match in re.finditer(month_pattern, text, re.IGNORECASE):
        try:
            month_str = match.group(1)
            day = int(match.group(2))
            year_str = match.group(3)
            
            # Get current year if not specified
            year = int(year_str) if year_str else datetime.now(timezone.utc).year
            
            # Parse month name
            month = _parse_month(month_str)
            
            dt = datetime(year, month, day, tzinfo=timezone.utc)
            if dt not in dates:
                dates.append(dt)
        except (ValueError, TypeError):
            continue
    
    # Relative patterns: "today", "yesterday", "last N days", "last N hours"
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    
    if re.search(r'\btoday\b', text, re.IGNORECASE):
        dates.append(now)
    
    if re.search(r'\byesterday\b', text, re.IGNORECASE):
        dates.append(now - timedelta(days=1))
    
    # "last N days"
    days_pattern = r'\blast\s+(\d+)\s+days?\b'
    for match in re.finditer(days_pattern, text, re.IGNORECASE):
        try:
            days = int(match.group(1))
            dates.append(now - timedelta(days=days))
        except (ValueError, TypeError):
            continue
    
    # "last N hours"
    hours_pattern = r'\blast\s+(\d+)\s+hours?\b'
    for match in re.finditer(hours_pattern, text, re.IGNORECASE):
        try:
            hours = int(match.group(1))
            dates.append(now - timedelta(hours=hours))
        except (ValueError, TypeError):
            continue
    
    # Remove duplicates and return
    return list(dict.fromkeys(dates))


def _parse_month(month_str: str) -> int:
    """Convert month name to month number (1-12)."""
    months = {
        'january': 1, 'jan': 1,
        'february': 2, 'feb': 2,
        'march': 3, 'mar': 3,
        'april': 4, 'apr': 4,
        'may': 5,
        'june': 6, 'jun': 6,
        'july': 7, 'jul': 7,
        'august': 8, 'aug': 8,
        'september': 9, 'sep': 9, 'sept': 9,
        'october': 10, 'oct': 10,
        'november': 11, 'nov': 11,
        'december': 12, 'dec': 12,
    }
    month = months.get(month_str.lower())
    if month is None:
        raise ValueError(f"Unknown month: {month_str}")
    return month


def format_date_for_api(dt: datetime) -> str:
    """
    Format datetime for Magnus API.
    
    Format: ISO 8601 with timezone: 2026-05-08T00:00:00.000
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000")
