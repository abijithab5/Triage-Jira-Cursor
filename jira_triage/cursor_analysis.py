from __future__ import annotations

import os
import subprocess
from pathlib import Path


class CursorAnalysisError(RuntimeError):
    """Raised when the Cursor agent subprocess fails."""

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _shim_path() -> Path:
    """Return the absolute path to run_cursor_agent.mjs."""
    return Path(__file__).resolve().parents[1] / "run_cursor_agent.mjs"


def run_cursor_analysis(
    *,
    context_path: Path,
    repo_root: Path,
    analysis_txt_path: Path,
    cursor_api_key: str,
    model_id: str = "composer-2",
    node_timeout_seconds: float = 300.0,
) -> str:
    """Run the Cursor agent against *context_path* and write the result.

    Calls ``node run_cursor_agent.mjs <context_path> <repo_root>`` as a
    subprocess, captures stdout, writes it to *analysis_txt_path* and the
    corresponding ``.md`` sibling, and returns the analysis text.

    Exit-code semantics (mirrored from the shim):
      1 - CursorAgentError (startup: auth/config/network)
      2 - run failed mid-execution
      3 - usage / filesystem error inside the shim

    Raises :class:`CursorAnalysisError` on any non-zero exit.
    """
    shim = _shim_path()
    if not shim.is_file():
        raise CursorAnalysisError(
            f"run_cursor_agent.mjs not found at {shim}. "
            "Make sure you have run `npm install` in the repo root."
        )

    env = {**os.environ, "CURSOR_API_KEY": cursor_api_key, "CURSOR_MODEL_ID": model_id}

    try:
        proc = subprocess.run(
            ["node", str(shim), str(context_path), str(repo_root)],
            capture_output=True,
            text=True,
            timeout=node_timeout_seconds,
            env=env,
        )
    except FileNotFoundError as e:
        raise CursorAnalysisError(
            "node executable not found. Install Node.js 18+ to use --cursor-analysis."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise CursorAnalysisError(
            f"Cursor agent timed out after {node_timeout_seconds}s."
        ) from e

    if proc.returncode != 0:
        stderr_snippet = (proc.stderr or "").strip()[:500]
        msg_parts = [f"Cursor agent subprocess exited with code {proc.returncode}."]
        if stderr_snippet:
            msg_parts.append(f"stderr: {stderr_snippet}")
        raise CursorAnalysisError(" ".join(msg_parts), exit_code=proc.returncode)

    analysis_text = proc.stdout
    if analysis_text and not analysis_text.endswith("\n"):
        analysis_text += "\n"

    analysis_txt_path.write_text(analysis_text, encoding="utf-8")

    analysis_md_path = analysis_txt_path.with_suffix(".md")
    try:
        analysis_md_path.write_text(analysis_text, encoding="utf-8")
    except Exception:
        pass

    return analysis_text
