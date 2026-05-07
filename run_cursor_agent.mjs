#!/usr/bin/env node
/**
 * run_cursor_agent.mjs
 *
 * Usage: node run_cursor_agent.mjs <context_path> <repo_root>
 *
 * Reads a context markdown file, sends it to the Cursor agent via Agent.prompt()
 * with the local runtime, and prints the analysis result to stdout.
 *
 * Exit codes:
 *   0 - success
 *   1 - CursorAgentError (startup failure: auth, config, network)
 *   2 - run failed (agent started but work failed)
 *   3 - usage / filesystem error
 */

import { Agent, CursorAgentError } from "@cursor/sdk";
import { readFileSync } from "fs";
import { resolve } from "path";

const [, , contextPathArg, repoRootArg] = process.argv;

if (!contextPathArg || !repoRootArg) {
  process.stderr.write(
    "Usage: node run_cursor_agent.mjs <context_path> <repo_root>\n"
  );
  process.exit(3);
}

const contextPath = resolve(contextPathArg);
const repoRoot = resolve(repoRootArg);

let contextMarkdown;
try {
  contextMarkdown = readFileSync(contextPath, "utf-8");
} catch (err) {
  process.stderr.write(
    `Failed to read context file ${contextPath}: ${err.message}\n`
  );
  process.exit(3);
}

const apiKey = process.env.CURSOR_API_KEY;
if (!apiKey || !apiKey.trim()) {
  process.stderr.write(
    "CURSOR_API_KEY environment variable is not set or empty.\n"
  );
  process.exit(1);
}

const modelId = process.env.CURSOR_MODEL_ID || "composer-2";

try {
  const result = await Agent.prompt(contextMarkdown, {
    apiKey: apiKey.trim(),
    model: { id: modelId },
    local: { cwd: repoRoot },
  });

  if (result.status === "error") {
    process.stderr.write(
      `Cursor agent run failed (run id: ${result.id ?? "unknown"}).\n`
    );
    process.exit(2);
  }

  const output =
    typeof result.result === "string"
      ? result.result
      : JSON.stringify(result.result ?? "", null, 2);

  process.stdout.write(output);
  if (output && !output.endsWith("\n")) {
    process.stdout.write("\n");
  }

  process.exit(0);
} catch (err) {
  if (err instanceof CursorAgentError) {
    process.stderr.write(
      `Cursor agent startup failed: ${err.message} (retryable=${err.isRetryable})\n`
    );
    process.exit(1);
  }
  process.stderr.write(`Unexpected error: ${err?.message ?? String(err)}\n`);
  process.exit(1);
}
