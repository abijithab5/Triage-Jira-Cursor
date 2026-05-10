"""Tests for incident date extraction from Jira descriptions."""

from __future__ import annotations

import unittest
from datetime import datetime

from jira_triage.date_extractor import extract_dates_from_description, _find_dates_in_text


class TestEuropeanDottedDates(unittest.TestCase):
    def test_dd_mm_yyyy_in_de_ticket_description(self) -> None:
        desc = (
            "*Error occurred on this date, at this time:* 07.05.2026 1:15 am\r\n"
            "today (07.05.2026) at 12:15 a.m.\r\n"
        )
        found = _find_dates_in_text(desc)
        self.assertTrue(any(d.date() == datetime(2026, 5, 7).date() for d in found))

    def test_extract_range_expands_one_day_before_min(self) -> None:
        desc = "Incident on 07.05.2026 evening"
        start, end = extract_dates_from_description(desc)
        self.assertEqual(end.date(), datetime(2026, 5, 7).date())
        self.assertEqual(start.date(), datetime(2026, 5, 6).date())


if __name__ == "__main__":
    unittest.main()
