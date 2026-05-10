from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .debug_log import debug_log


def get_file_hash(filepath: Path) -> str:
    """Calculate MD5 hash of file for deduplication."""
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def strip_date_prefix(filename: str) -> str:
    """
    Strip date/time prefixes from filenames to find the base log name.
    Examples:
    - 20250509123456_Consolelog.txt.0 -> Consolelog.txt.0
    - 2025-05-09_12-34-56_Consolelog.txt.0 -> Consolelog.txt.0
    """
    patterns = [
        r'^\d{14}_',
        r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_',
        r'^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}_',
        r'^\d{8}_\d{6}_',
        r'^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z?_',
    ]
    
    res = filename
    for pattern in patterns:
        res = re.sub(pattern, '', res)
        if res != filename:
            break
            
    return res


_TZ_UTC = ZoneInfo("UTC")
_TZ_CET = ZoneInfo("Europe/Berlin")
_TIMESTAMP_PATTERN = re.compile(
    r'(?<!\d)\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?!\d)|'
    r'(?<!\d)\d{6}-\d{2}:\d{2}:\d{2}(?!\d)|'
    r'\b[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2}(?!\d)|'
    r'(?<!\d)\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}(?!\d)|'
    r'(?<!\d)\d{14}(?!\d)|'
    r'(?<!\d)\d{8}_\d{6}(?!\d)'
)

def convert_timestamps_to_cet(text: str, year_hint: int = 2026) -> str:
    """
    Convert all recognized UTC timestamps in the text to CET/CEST.
    Uses a cache to speed up conversion of repeated timestamps.
    """
    cache: dict[str, str] = {}
    
    def replace_match(match: re.Match) -> str:
        ts_str = match.group(0)
        if ts_str in cache:
            return cache[ts_str]
            
        try:
            if ts_str[4] == '-':
                if 'T' in ts_str:
                    dt = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
                    dt = dt.replace(tzinfo=_TZ_UTC).astimezone(_TZ_CET)
                    res = dt.strftime("%Y-%m-%dT%H:%M:%S") + ts_str[19:]
                elif ' ' in ts_str:
                    dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=_TZ_UTC).astimezone(_TZ_CET)
                    res = dt.strftime("%Y-%m-%d %H:%M:%S") + ts_str[19:]
                else:
                    # YYYY-MM-DD-HH-MM-SS
                    dt = datetime.strptime(ts_str, "%Y-%m-%d-%H-%M-%S")
                    dt = dt.replace(tzinfo=_TZ_UTC).astimezone(_TZ_CET)
                    res = dt.strftime("%Y-%m-%d-%H-%M-%S")
            elif ts_str[6] == '-':
                # YYMMDD-HH:MM:SS
                dt = datetime.strptime(ts_str[:15], "%y%m%d-%H:%M:%S")
                dt = dt.replace(tzinfo=_TZ_UTC).astimezone(_TZ_CET)
                res = dt.strftime("%y%m%d-%H:%M:%S") + ts_str[15:]
            elif ts_str[0].isalpha():
                # MMM  D HH:MM:SS
                dt_str = f"{year_hint} {ts_str}"
                dt_str_normalized = re.sub(r'\s+', ' ', dt_str)
                dt = datetime.strptime(dt_str_normalized, "%Y %b %d %H:%M:%S")
                dt = dt.replace(tzinfo=_TZ_UTC).astimezone(_TZ_CET)
                day_str = str(dt.day).rjust(2, ' ')
                res = dt.strftime(f"%b {day_str} %H:%M:%S")
            elif '_' in ts_str:
                # YYYYMMDD_HHMMSS
                dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
                dt = dt.replace(tzinfo=_TZ_UTC).astimezone(_TZ_CET)
                res = dt.strftime("%Y%m%d_%H%M%S")
            else:
                # YYYYMMDDHHMMSS
                dt = datetime.strptime(ts_str, "%Y%m%d%H%M%S")
                dt = dt.replace(tzinfo=_TZ_UTC).astimezone(_TZ_CET)
                res = dt.strftime("%Y%m%d%H%M%S")
                
            cache[ts_str] = res
            return res
        except Exception:
            cache[ts_str] = ts_str
            return ts_str

    return _TIMESTAMP_PATTERN.sub(replace_match, text)


def _try_zip_extract(filepath: Path) -> bool:
    """If filepath is a ZIP archive, extract next to stem and delete the file. Returns True on success."""
    try:
        if not zipfile.is_zipfile(filepath):
            return False
        extract_path = filepath.parent / filepath.stem
        extract_path.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(filepath, "r") as zip_ref:
            zip_ref.extractall(extract_path)
        filepath.unlink(missing_ok=True)
        return True
    except Exception as e:
        debug_log(
            run_id="log-merger",
            hypothesis_id="extraction_zip",
            location="jira_triage/log_merger.py:_try_zip_extract",
            message=f"ZIP extraction failed for {filepath.name}: {e}",
            data={"filepath": str(filepath)},
        )
        return False


def _try_tar_extract(filepath: Path) -> bool:
    """If filepath is a tar archive (incl. tar.gz/.tgz), extract and delete. Returns True on success."""
    try:
        if not tarfile.is_tarfile(filepath):
            return False
        extract_path = filepath.parent / filepath.stem
        extract_path.mkdir(parents=True, exist_ok=True)
        with tarfile.open(filepath, "r:*") as tar_ref:
            tar_ref.extractall(extract_path)
        filepath.unlink(missing_ok=True)
        return True
    except Exception as e:
        debug_log(
            run_id="log-merger",
            hypothesis_id="extraction_tar",
            location="jira_triage/log_merger.py:_try_tar_extract",
            message=f"TAR extraction failed for {filepath.name}: {e}",
            data={"filepath": str(filepath)},
        )
        return False


def extract_nested_archives(input_dir: Path, output_dir: Path) -> None:
    """Recursively extract ZIP and TGZ/TAR archives in input_dir to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted = True
    current_in_dir = input_dir
    iteration = 0

    while extracted:
        extracted = False
        iteration += 1
        
        files_to_process = list(current_in_dir.rglob("*"))
        debug_log(
            run_id="log-merger",
            hypothesis_id="extraction_loop",
            location="jira_triage/log_merger.py:extract_nested_archives",
            message=f"Extraction iteration {iteration}: found {len(files_to_process)} items",
            data={"iteration": iteration, "items_count": len(files_to_process)},
        )
        
        for filepath in files_to_process:
            if not filepath.is_file():
                continue

            try:
                suffix = filepath.suffix.lower()
                handled = False
                if suffix == ".zip":
                    handled = _try_zip_extract(filepath)
                elif suffix in [".tgz", ".gz", ".tar"]:
                    # Prefer tar semantics for typical .tgz; Magnus sometimes stores ZIP under .tgz.
                    handled = _try_tar_extract(filepath)
                    if (
                        not handled
                        and suffix in (".tgz", ".tar")
                        and _try_zip_extract(filepath)
                    ):
                        handled = True
                
                if handled:
                    debug_log(
                        run_id="log-merger",
                        hypothesis_id="extraction_success",
                        location="jira_triage/log_merger.py:extract_nested_archives",
                        message=f"Successfully extracted {filepath.name}",
                        data={"filepath": str(filepath)},
                    )
                    extracted = True
            except Exception as e:
                debug_log(
                    run_id="log-merger",
                    hypothesis_id="extraction_error",
                    location="jira_triage/log_merger.py:extract_nested_archives",
                    message=f"Failed to extract {filepath.name}: {e}",
                    data={"filepath": str(filepath)},
                )
        current_in_dir = output_dir


def merge_logs_by_category(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    """
    Extract, group by base filename, merge, and deduplicate logs from input_dir to output_dir.

    Args:
        input_dir: Directory containing raw (potentially nested) log archives/files
        output_dir: Directory to place merged logs and metadata

    Returns:
        dict: Metadata dictionary about the merging process
    """
    debug_log(
        run_id="log-merger",
        hypothesis_id="start",
        location="jira_triage/log_merger.py:merge_logs_by_category",
        message="Starting log merging",
        data={"input_dir": str(input_dir), "output_dir": str(output_dir)},
    )

    # 1. Prepare extraction dir and copy files there to keep input intact
    extracted_dir = output_dir / "extracted_temp"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    for item in input_dir.iterdir():
        if item.is_file():
            shutil.copy2(item, extracted_dir / item.name)
        elif item.is_dir() and item.name != "extracted_temp":
            shutil.copytree(item, extracted_dir / item.name, dirs_exist_ok=True)

    extract_nested_archives(extracted_dir, extracted_dir)

    # 2. Categorize and merge
    merged_dir = output_dir
    merged_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir = merged_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    files_by_group: dict[str, list[Path]] = defaultdict(list)
    stats: dict[str, int] = defaultdict(int)

    for filepath in extracted_dir.rglob("*"):
        if filepath.is_file():
            # Skip hidden files or metadata files that might have been created
            if filepath.name.startswith("."):
                continue
            
            group_name = strip_date_prefix(filepath.name)
            files_by_group[group_name].append(filepath)
            stats["total_files"] += 1

    group_stats: dict[str, dict[str, int | float]] = {}

    for group_name in sorted(files_by_group.keys()):
        files = files_by_group[group_name]
        # Dedupe only within this log category. A global hash map incorrectly drops
        # whole groups when empty (or identical) files share MD5 across categories
        # (e.g. every BootTime.log empty after ArmConsolelog.txt.0 merged an empty file).
        file_hashes: dict[str, str] = {}

        # Create a safe filename for the merged output
        safe_group_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', group_name)
        output_file = merged_dir / f"{safe_group_name}"

        merged_count = 0
        total_size = 0
        deduplicated = 0

        with open(output_file, "w", encoding="utf-8", errors="ignore") as out_f:
            # Sort files by their original name (which usually contains the date prefix, ensuring chronological order)
            for filepath in sorted(files, key=lambda p: p.name):
                file_hash = get_file_hash(filepath)

                # Skip duplicates
                if file_hash in file_hashes:
                    deduplicated += 1
                    continue

                file_hashes[file_hash] = str(filepath)

                try:
                    if zipfile.is_zipfile(filepath) or tarfile.is_tarfile(filepath):
                        debug_log(
                            run_id="log-merger",
                            hypothesis_id="skip_unpackable_archive",
                            location="jira_triage/log_merger.py:merge_logs_by_category",
                            message=(
                                f"Skipping raw archive remaining after extraction attempts: "
                                f"{filepath.name} (would corrupt merged text)."
                            ),
                            data={"filepath": str(filepath)},
                        )
                        stats["skipped_binary_archives"] += 1
                        continue

                    file_size = filepath.stat().st_size
                    total_size += file_size

                    # Write file separator and header as requested: ****Merging <original_filename> **********
                    # Also convert the timestamp in the filename if present
                    header_line = f"****Merging {filepath.name} **********\n"
                    out_f.write(convert_timestamps_to_cet(header_line))

                    # Write file content with converted timestamps
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as in_f:
                        content = in_f.read()
                        converted_content = convert_timestamps_to_cet(content)
                        out_f.write(converted_content)
                        out_f.write("\n")

                    merged_count += 1
                    stats["merged_files"] += 1

                except Exception as e:
                    debug_log(
                        run_id="log-merger",
                        hypothesis_id="read_error",
                        location="jira_triage/log_merger.py:merge_logs_by_category",
                        message=f"Error reading {filepath.name}: {e}",
                        data={"filepath": str(filepath)},
                    )
                    stats["errors"] += 1

        output_size = output_file.stat().st_size
        group_stats[group_name] = {
            "files": merged_count,
            "size": output_size,
            "deduplicated": deduplicated,
        }

    # Generate metadata
    metadata = {
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "input_directory": str(input_dir),
        "output_directory": str(output_dir),
        "statistics": {
            "total_files_processed": stats["total_files"],
            "files_merged": stats["merged_files"],
            "files_deduplicated": stats["total_files"] - stats["merged_files"],
            "skipped_binary_archives": stats["skipped_binary_archives"],
            "errors": stats["errors"],
        },
        "groups": {},
    }

    for group, g_stats in sorted(group_stats.items()):
        metadata["groups"][group] = {
            "merged_files": g_stats["files"],
            "output_size_bytes": g_stats["size"],
            "output_size_mb": round(g_stats["size"] / 1024 / 1024, 2),
            "deduplicated": g_stats["deduplicated"],
        }

    metadata_file = metadata_dir / "merge_metadata.json"
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    summary_file = metadata_dir / "merge_summary.txt"
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write("Log Merge Summary\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"Processed: {metadata['processed_at']}\n")
        f.write(f"Input: {input_dir}\n")
        f.write(f"Output: {output_dir}\n\n")

        f.write("Statistics\n")
        f.write("-" * 70 + "\n")
        f.write(f"Total files found: {metadata['statistics']['total_files_processed']}\n")
        f.write(f"Files merged: {metadata['statistics']['files_merged']}\n")
        f.write(f"Duplicates removed: {metadata['statistics']['files_deduplicated']}\n")
        f.write(
            f"Skipped binary archives (not expanded): {metadata['statistics']['skipped_binary_archives']}\n"
        )
        f.write(f"Errors: {metadata['statistics']['errors']}\n\n")

        f.write("Groups\n")
        f.write("-" * 70 + "\n")
        for group, m_stats in metadata["groups"].items():
            f.write(f"{group[:30]:30} | {m_stats['merged_files']:3} files | {m_stats['output_size_mb']:6.2f} MB\n")

    # Cleanup extracted temp dir
    shutil.rmtree(extracted_dir, ignore_errors=True)

    return metadata
