from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class RepoError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepoPathsSuggestion:
    path: str
    score: int
    reasons: list[str]


def _git_top_level(cwd: Path) -> Path | None:
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    if p.returncode != 0:
        return None
    s = (p.stdout or "").strip()
    if not s:
        return None
    return Path(s)


def resolve_repo_root(repo: str | None) -> Path:
    """
    Resolve the repo root used for writing `.cursor/context/TICKET.md` and `out/<KEY>/...`.

    Priority:
    - explicit `repo` argument
    - git top-level of current working directory
    - current working directory
    """
    if repo is not None and str(repo).strip():
        p = Path(str(repo)).expanduser()
        if p.is_file():
            p = p.parent
        if not p.exists():
            raise RepoError(f"Repo path does not exist: {p}")
        return p.resolve()

    cwd = Path.cwd()
    git_root = _git_top_level(cwd)
    if git_root is not None:
        return git_root.resolve()
    return cwd.resolve()


def list_repo_files(repo_root: Path) -> list[str]:
    """
    Prefer git tracked files; fall back to a conservative directory walk.
    Returns repo-relative POSIX-like paths.
    """
    repo_root = repo_root.resolve()
    try:
        p = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
        )
    except Exception:
        p = None

    if p is not None and p.returncode == 0 and p.stdout:
        parts = p.stdout.split(b"\x00")
        out: list[str] = []
        for b in parts:
            if not b:
                continue
            try:
                s = b.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if s:
                out.append(s)
        return out

    # Fallback walk (bounded)
    skip_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cursor", "dist", "build", "out"}
    out: list[str] = []
    max_files = 50_000
    for root, dirs, files in os.walk(repo_root):
        rel_root = Path(root).relative_to(repo_root)
        # mutate dirs in-place to prune
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            pth = (rel_root / name).as_posix()
            out.append(pth)
            if len(out) >= max_files:
                return out
    return out


_KW_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./:-]{2,}")


def extract_keywords(texts: Iterable[str], *, max_keywords: int = 50) -> list[str]:
    """
    Extract simple keywords for repo/path relevance scoring.
    """
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "then",
        "when",
        "into",
        "your",
        "you",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "not",
        "none",
        "null",
        "true",
        "false",
        "http",
        "https",
        "www",
        "com",
        "org",
        "net",
        "jira",
        "cursor",
        "error",
        "exception",
        "traceback",
        "failed",
        "failure",
        "stack",
        "trace",
        "request",
        "response",
        "status",
    }
    seen: set[str] = set()
    out: list[str] = []
    for t in texts:
        if not t:
            continue
        for m in _KW_RE.finditer(t):
            kw = m.group(0)
            kw_norm = kw.strip().strip(":").strip("/").lower()
            if not kw_norm or kw_norm in stop:
                continue
            if len(kw_norm) < 3:
                continue
            if kw_norm in seen:
                continue
            seen.add(kw_norm)
            out.append(kw_norm)
            if len(out) >= max_keywords:
                return out
    return out


def suggest_repo_paths(
    repo_root: Path,
    *,
    keywords: list[str],
    max_paths: int = 20,
) -> list[RepoPathsSuggestion]:
    """
    Suggest relevant repo paths using lightweight path+content scoring.
    """
    files = list_repo_files(repo_root)
    if not files:
        return []

    code_exts = {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".java",
        ".kt",
        ".cs",
        ".rb",
        ".php",
        ".rs",
        ".scala",
        ".swift",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
    }
    prefer_paths = ("src/", "app/", "services/", "server/", "backend/", "api/", "pkg/", "cmd/")

    kws = [k for k in keywords if k and len(k) >= 3][:50]

    def path_score(rel: str) -> tuple[int, list[str]]:
        s = rel.lower()
        reasons: list[str] = []
        score = 0
        for k in kws:
            if k in s:
                score += 5
                reasons.append(f"path:{k}")
        if s.startswith(prefer_paths):
            score += 2
        ext = Path(rel).suffix.lower()
        if ext in code_exts:
            score += 1
        return score, reasons

    scored: list[tuple[int, str, list[str]]] = []
    for rel in files:
        score, reasons = path_score(rel)
        if score <= 0:
            continue
        scored.append((score, rel, reasons))

    scored.sort(reverse=True, key=lambda t: (t[0], -len(t[1])))
    top = scored[: max_paths * 5]

    # Content boost for top candidates (bounded)
    boosted: list[RepoPathsSuggestion] = []
    max_bytes = 200_000
    for base_score, rel, reasons in top:
        p = (repo_root / rel)
        if not p.is_file():
            continue
        try:
            if p.stat().st_size > max_bytes:
                boosted.append(RepoPathsSuggestion(path=rel, score=base_score, reasons=reasons))
                continue
            raw = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            boosted.append(RepoPathsSuggestion(path=rel, score=base_score, reasons=reasons))
            continue
        content_l = raw.lower()
        score = base_score
        content_hits = 0
        for k in kws[:15]:
            if k in content_l:
                score += 2
                content_hits += 1
                if content_hits >= 5:
                    break
        boosted.append(RepoPathsSuggestion(path=rel, score=score, reasons=reasons))

    boosted.sort(reverse=True, key=lambda s: (s.score, -len(s.path)))
    # Deduplicate by path
    seen: set[str] = set()
    out: list[RepoPathsSuggestion] = []
    for s in boosted:
        if s.path in seen:
            continue
        seen.add(s.path)
        out.append(s)
        if len(out) >= max_paths:
            break
    return out

