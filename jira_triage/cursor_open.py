from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from shutil import which


def open_in_cursor(target: Path) -> bool:
    """
    Best-effort open in Cursor.
    Returns True if the command was invoked, False if unavailable or failed.
    """
    exe = which("cursor")
    if exe:
        try:
            subprocess.run([exe, str(target)], check=False)
            return True
        except Exception:
            return False

    # macOS fallback (Cursor app without CLI installed)
    if sys.platform == "darwin":
        open_exe = which("open")
        if not open_exe:
            return False
        for app in ("Cursor", "Cursor.app"):
            try:
                p = subprocess.run([open_exe, "-a", app, str(target)], check=False, capture_output=True, text=True)
                if p.returncode == 0:
                    return True
            except Exception:
                continue

    return False

