"""Tests for Magnus/log archive merging (ZIP vs TGZ payloads)."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from jira_triage.log_merger import extract_nested_archives, merge_logs_by_category


class TestLogMerger(unittest.TestCase):
    def test_zip_saved_as_tgz_is_extracted(self) -> None:
        """Magnus downloads use a `.tgz` filename but payloads may be ZIP."""
        with tempfile.TemporaryDirectory() as td_raw:
            td = Path(td_raw)
            ext = td / "extracted"
            ext.mkdir(parents=True, exist_ok=True)
            arch = ext / "magnus_logs_FOO_999.tgz"
            with zipfile.ZipFile(arch, "w") as zf:
                zf.writestr("bundle/log.txt", "Speedport syslog line\nout of memory killer\nERROR test\n")

            extract_nested_archives(ext, ext)

            self.assertFalse(arch.exists(), "archive should be removed after extraction")
            inner = next(ext.rglob("log.txt"))
            self.assertIn("Speedport syslog line", inner.read_text(encoding="utf-8", errors="replace"))

    def test_merge_zip_as_tgz_produces_readable_merged_logs(self) -> None:
        with tempfile.TemporaryDirectory() as td_raw:
            td = Path(td_raw)
            inp = td / "in"
            out = td / "merged"
            inp.mkdir(parents=True, exist_ok=True)
            fake = inp / "magnus_logs_FOO_123.tgz"
            with zipfile.ZipFile(fake, "w") as zf:
                zf.writestr(
                    "CPE/console.log",
                    "Jan 05 10:00:00 box rdkb: [RDKB_PLATFORM_ERROR] demo\n",
                )

            meta = merge_logs_by_category(inp, out)
            self.assertEqual(meta["statistics"]["skipped_binary_archives"], 0)
            merged_path = out / "console.log"
            self.assertTrue(merged_path.is_file(), f"expected merged log at {merged_path}")
            blob = merged_path.read_text(encoding="utf-8", errors="replace")
            self.assertIn("RDKB_PLATFORM_ERROR", blob)

    def test_per_group_dedup_keeps_same_content_across_log_types(self) -> None:
        """MD5 deduplication must not apply across categories (e.g. two empty logs)."""
        with tempfile.TemporaryDirectory() as td_raw:
            td = Path(td_raw)
            inp = td / "in"
            out = td / "merged"
            inp.mkdir(parents=True, exist_ok=True)
            (inp / "2026-05-02-00-00-00_ArmConsolelog.txt.0").write_text("", encoding="utf-8")
            (inp / "2026-05-02-00-00-00_BootTime.log").write_text("", encoding="utf-8")

            merge_logs_by_category(inp, out)
            boot = out / "BootTime.log"
            arm = out / "ArmConsolelog.txt.0"
            self.assertTrue(boot.is_file(), "BootTime.log should be merged even if empty like ArmConsole")
            self.assertTrue(arm.is_file())
            self.assertIn("Merging", boot.read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
