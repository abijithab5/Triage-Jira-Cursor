"""Regression tests for log summarization regex patterns."""

from __future__ import annotations

import unittest

from jira_triage.logs_processing import (
    normalize_logs_text,
    summarize_logs,
)


class TestLogsProcessingPatterns(unittest.TestCase):
    def test_trace_and_request_ids_extracted(self) -> None:
        text = """line0
trace_id=abcdef0123456789
x-request-id: 11112222aaaa-bbbb
"""
        s = summarize_logs(text)
        self.assertIn("abcdef0123456789", s["trace_ids"])
        self.assertIn("11112222aaaa-bbbb", s["request_ids"])

    def test_http_calls_extracted(self) -> None:
        text = '2026-05-09 10:00:00 INFO GET /rest/api/foo HTTP/1.1"'
        s = summarize_logs(text)
        self.assertEqual(len(s["http_calls"]), 1)
        self.assertEqual(s["http_calls"][0]["method"], "GET")
        self.assertEqual(s["http_calls"][0]["target"], "/rest/api/foo")

    def test_python_stack_file_extracted(self) -> None:
        text = '  File "app/main.py", line 42, in handler\n'
        s = summarize_logs(text)
        self.assertTrue(any(h.get("kind") == "python" for h in s["stack_hints"]))

    def test_bearer_redaction(self) -> None:
        raw = "Authorization: Bearer eyJabcdefghijklmnop"
        out = normalize_logs_text(raw)
        self.assertNotIn("eyJabc", out)
        self.assertIn("<REDACTED>", out)

    def test_no_local_logs_stub_sets_ingestion_status(self) -> None:
        text = (
            "===== FILE: /tmp/NO_LOCAL_LOGS_PLACEHOLDER.txt =====\n"
            "===== NO LOG-LIKE FILES IN LOCAL LOG SOURCE =====\n"
            "Ticket: ABC-1\n"
        )
        s = summarize_logs(text)
        self.assertEqual(s.get("ingestion_status"), "no_local_logs_stub")
        sig = s.get("signals") or {}
        assert isinstance(sig, dict)
        self.assertTrue((sig.get("no_ingested_device_logs") or {}).get("stub"))


if __name__ == "__main__":
    unittest.main()
