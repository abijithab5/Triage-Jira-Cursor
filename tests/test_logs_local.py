"""Tests for local log folder collection (LOGS_DIR fallback)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jira_triage.logs_local import collect_local_logs, has_ingestible_local_logs


class TestHasIngestibleLocalLogs(unittest.TestCase):
    def test_dotfiles_only_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            logs_dir = Path(td) / "logs"
            logs_dir.mkdir()
            (logs_dir / ".DS_Store").write_text("x", encoding="utf-8")
            self.assertFalse(has_ingestible_local_logs(logs_dir, "ABC-999"))

    def test_log_file_returns_true(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            logs_dir = Path(td) / "logs"
            logs_dir.mkdir()
            (logs_dir / "device.log").write_text("ok\n", encoding="utf-8")
            self.assertTrue(has_ingestible_local_logs(logs_dir, "T-1"))

    def test_missing_dir_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(has_ingestible_local_logs(Path(td) / "nope", "T-1"))


class TestCollectLocalLogs(unittest.TestCase):
    def test_missing_logs_dir_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ticket_dir = Path(td) / "out" / "T-1"
            ticket_dir.mkdir(parents=True)
            res = collect_local_logs(
                ticket_dir=ticket_dir,
                logs_dir=Path(td) / "nonexistent_logs",
                ticket_key="T-1",
            )
        self.assertFalse(res.ok)
        self.assertIsNotNone(res.error)
        assert res.error is not None
        self.assertIn("does not exist", res.error)

    def test_existing_dir_only_dotfiles_returns_ok_with_stub(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            logs_dir = Path(td) / "logs"
            logs_dir.mkdir()
            (logs_dir / ".DS_Store").write_text("x", encoding="utf-8")
            ticket_dir = Path(td) / "out" / "ABC-999"
            ticket_dir.mkdir(parents=True)
            res = collect_local_logs(ticket_dir=ticket_dir, logs_dir=logs_dir, ticket_key="ABC-999")
            self.assertTrue(res.ok, msg=res.error)
            assert res.combined_path is not None
            text = res.combined_path.read_text(encoding="utf-8")
            self.assertIn("NO LOG-LIKE FILES IN LOCAL LOG SOURCE", text)
            self.assertIn("ds_store", text.lower())
            assert res.copied_paths and len(res.copied_paths) == 1
            self.assertTrue(res.copied_paths[0].is_file())


if __name__ == "__main__":
    unittest.main()
