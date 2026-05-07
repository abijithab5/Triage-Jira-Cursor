## Local triage workspace (not committed)

This folder is used as a **local working area** by `jira_triage/setup_and_run.sh`:

- `repo/`: your codebase checkout (or a pointer via `--repo`)
- `logs/`: log bundles you want to triage
- `out/`: generated triage artifacts (context, cleaned logs, summaries, analysis)

These subfolders are intentionally ignored by git (see the repo `.gitignore`).

