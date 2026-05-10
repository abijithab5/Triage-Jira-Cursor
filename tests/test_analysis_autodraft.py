"""Tests for analysis auto-draft (merged logs dir + ticket hints)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jira_triage.core import _analysis_autodraft


class TestAnalysisAutodraft(unittest.TestCase):
    def test_autodraft_includes_ticket_and_empty_logs_omits_file_list(self) -> None:
        issue = {
            "fields": {
                "description": "*BaseMac:* 34:19:4d:c7:3f:0f\r\n\r\n*User:* AB5",
                "summary": "Sample",
                "status": {"name": "Open"},
            }
        }
        text = _analysis_autodraft(
            ticket_key="RDK-1",
            jira_base_url="https://jira.example",
            issue=issue,
            jira_source_used="api",
            logs_dir_path=None,
            suggested_paths=None,
        )
        self.assertIn("RDK-1", text)
        self.assertIn("https://jira.example/browse/RDK-1", text)
        self.assertIn("Auto-draft.", text)
        self.assertIn("*BaseMac:*", text)
        self.assertNotIn("Merged Logs", text)

    def test_autodraft_lists_merged_files_and_merge_metadata(self) -> None:
        issue = {"fields": {"summary": "T", "status": {"name": "Open"}}}
        with tempfile.TemporaryDirectory() as td:
            logs_root = Path(td)
            (logs_root / "syslog.txt").write_text("x\n", encoding="utf-8")
            meta = logs_root / "metadata"
            meta.mkdir()
            (meta / "merge_summary.txt").write_text("ok\n", encoding="utf-8")
            text = _analysis_autodraft(
                ticket_key="RDK-2",
                jira_base_url="https://jira.example",
                issue=issue,
                jira_source_used="api",
                logs_dir_path=logs_root,
                suggested_paths=[{"path": "jira_triage/core.py", "score": 1.0, "reasons": ["hit"]}],
            )
        self.assertIn("`syslog.txt`", text)
        self.assertIn("`metadata/merge_summary.txt`", text)
        self.assertIn("`jira_triage/core.py`", text)

    def test_autodraft_skips_dotfiles_and_json_in_merged_dir(self) -> None:
        issue = {"fields": {}}
        with tempfile.TemporaryDirectory() as td:
            lr = Path(td)
            (lr / "visible.log").write_text("a", encoding="utf-8")
            (lr / ".hidden").write_text("b", encoding="utf-8")
            (lr / "foo.json").write_text("{}", encoding="utf-8")
            text = _analysis_autodraft(
                ticket_key="RDK-3",
                jira_base_url="https://jira.example",
                issue=issue,
                jira_source_used=None,
                logs_dir_path=lr,
                suggested_paths=None,
            )
        self.assertIn("`visible.log`", text)
        self.assertNotIn("`.hidden`", text)
        self.assertNotIn("`foo.json`", text)


if __name__ == "__main__":
    unittest.main()
