from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from queue import Queue, Empty
from typing import Any, Deque


class McpError(RuntimeError):
    pass


@dataclass(frozen=True)
class McpServerConfig:
    command: str
    args: list[str]
    env: dict[str, str]
    timeout_seconds: float = 60.0


def _default_cursor_mcp_config_path() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def load_cursor_mcp_server(server_name: str, *, cursor_mcp_config_path: Path | None = None) -> McpServerConfig | None:
    """
    Best-effort loader for Cursor's MCP server config (~/.cursor/mcp.json).

    This file may contain secrets in the `env` section; callers MUST NOT log them.
    """
    cfg_path = cursor_mcp_config_path or _default_cursor_mcp_config_path()
    try:
        raw = cfg_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception as e:
        raise McpError(f"Failed to read Cursor MCP config at {cfg_path}: {e}") from e

    try:
        doc = json.loads(raw)
    except Exception as e:
        raise McpError(f"Cursor MCP config at {cfg_path} is not valid JSON: {e}") from e

    servers = doc.get("mcpServers")
    if not isinstance(servers, dict):
        return None

    entry = servers.get(server_name)
    if not isinstance(entry, dict):
        return None

    command = entry.get("command")
    args = entry.get("args")
    if not isinstance(command, str) or not command.strip():
        raise McpError(f"Invalid MCP server config for {server_name!r}: missing command")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise McpError(f"Invalid MCP server config for {server_name!r}: args must be a list[str]")

    env = entry.get("env")
    env_dict: dict[str, str] = {}
    if isinstance(env, dict):
        for k, v in env.items():
            if isinstance(k, str) and isinstance(v, str):
                env_dict[k] = v

    timeout_raw = entry.get("timeout")
    timeout_seconds = 60.0
    if isinstance(timeout_raw, (int, float)) and timeout_raw > 0:
        timeout_seconds = float(timeout_raw)

    return McpServerConfig(command=command, args=list(args), env=env_dict, timeout_seconds=timeout_seconds)


def _now() -> float:
    return time.monotonic()


class StdioMcpClient:
    """
    Minimal MCP (JSON-RPC) client over stdio (newline-delimited JSON).
    """

    def __init__(self, server: McpServerConfig):
        self._server = server
        self._proc: subprocess.Popen[str] | None = None
        self._id = 0
        self._rx: "Queue[dict[str, Any]]" = Queue()
        self._stderr_tail: Deque[str] = deque(maxlen=100)
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def __enter__(self) -> "StdioMcpClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        if self._proc is not None:
            return

        env = os.environ.copy()
        env.update(self._server.env)
        try:
            self._proc = subprocess.Popen(
                [self._server.command, *self._server.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except FileNotFoundError as e:
            raise McpError(f"MCP server command not found: {self._server.command!r}") from e
        except Exception as e:
            raise McpError(f"Failed to start MCP server: {e}") from e

        assert self._proc.stdout is not None
        assert self._proc.stderr is not None

        self._stdout_thread = threading.Thread(target=self._stdout_pump, name="mcp-stdout", daemon=True)
        self._stdout_thread.start()

        self._stderr_thread = threading.Thread(target=self._stderr_pump, name="mcp-stderr", daemon=True)
        self._stderr_thread.start()

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        try:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        finally:
            return

    def _stdout_pump(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            s = (line or "").strip()
            if not s:
                continue
            try:
                msg = json.loads(s)
            except Exception:
                continue
            if isinstance(msg, list):
                for item in msg:
                    if isinstance(item, dict):
                        self._rx.put(item)
            elif isinstance(msg, dict):
                self._rx.put(msg)

    def _stderr_pump(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr_tail.append(line.rstrip("\n"))

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpError("MCP server is not running")
        s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            proc.stdin.write(s + "\n")
            proc.stdin.flush()
        except Exception as e:
            raise McpError(f"Failed to write to MCP server stdin: {e}") from e

    def request(self, method: str, params: dict[str, Any] | None = None, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        req_id = self._next_id()
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        deadline = _now() + (timeout_seconds if timeout_seconds is not None else self._server.timeout_seconds)
        while _now() < deadline:
            try:
                msg = self._rx.get(timeout=0.25)
            except Empty:
                if self._proc is not None and self._proc.poll() is not None:
                    break
                continue
            if msg.get("id") == req_id:
                if "error" in msg:
                    raise McpError(f"MCP error response for {method}: {msg.get('error')}")
                result = msg.get("result")
                if not isinstance(result, dict):
                    raise McpError(f"Invalid MCP result for {method}: {msg}")
                return result
            # Ignore unrelated messages (notifications or other responses)
            continue

        tail = "\n".join(list(self._stderr_tail)[-20:])
        raise McpError(f"Timed out waiting for MCP response to {method}. stderr tail:\n{tail}".rstrip())

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self, *, protocol_version: str = "2025-03-26") -> dict[str, Any]:
        result = self.request(
            "initialize",
            params={
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "jira-triage", "version": "0.2.0"},
            },
        )
        self.notify("notifications/initialized", params={})
        return result

