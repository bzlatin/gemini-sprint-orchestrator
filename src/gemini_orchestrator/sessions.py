"""Private local conversation bridge and VS Code worktree workspaces."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import platform
import secrets
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from .config import atomic_write
from .engine import Engine
from .git import command
from .models import OrchestratorError, State, digest

MAX_REQUEST = 64 * 1024


class SessionBridge:
    def __init__(self, engine: Engine, *, connected_enabled: bool = False):
        self.engine = engine
        self.connected_enabled = connected_enabled
        self.descriptor = engine.runtime / "conversation-bridge.json"
        self.token = secrets.token_urlsafe(32)
        self.server: asyncio.Server | None = None
        self.directory: tempfile.TemporaryDirectory[str] | None = None
        self.handlers: set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        if self.server is not None:
            return
        # macOS Unix sockets have a short path limit; the profile path can be much longer.
        self.directory = tempfile.TemporaryDirectory(prefix="gso-chat-", dir="/tmp")
        socket = Path(self.directory.name) / "session.sock"
        try:
            self.server = await asyncio.start_unix_server(
                self.handle, path=socket, limit=MAX_REQUEST
            )
            socket.chmod(0o600)
            atomic_write(self.descriptor, json.dumps({"socket": str(socket), "token": self.token}))
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        pending = list(self.handlers)
        for handler in pending:
            handler.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if self.descriptor.is_file() and not self.descriptor.is_symlink():
            try:
                if json.loads(self.descriptor.read_text()).get("token") == self.token:
                    self.descriptor.unlink()
            except (OSError, ValueError):
                pass
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None

    def pause_preview(self, key: str) -> dict[str, Any]:
        row = self.engine.store.row(key)
        if row["state"] not in {State.IN_PROGRESS, State.VERIFYING} or key not in self.engine.tasks:
            raise OrchestratorError("This worker is not running; refresh its session")
        self.engine.validate_worktree(row)
        return {
            "key": key,
            "branch": row["branch"],
            "pid": row["pid"],
            "process": row["process_token"],
            "state": row["state"],
        }

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request.get("token"), str) or not hmac.compare_digest(
            request["token"].encode(), self.token.encode()
        ):
            raise OrchestratorError("Session connection expired; reopen it from the dashboard")
        key = request.get("key")
        if not isinstance(key, str) or len(key) > 100:
            raise OrchestratorError("Choose a valid ticket key")
        action = request.get("action")
        if action == "snapshot":
            return self.engine.session_snapshot(key)
        if action in {"preview", "send"}:
            message = request.get("message")
            if not isinstance(message, str):
                raise OrchestratorError("Enter a text message")
            preview = self.engine.session_preview(key, message)
            fingerprint = digest(preview)
            if action == "preview":
                return {"preview": preview, "fingerprint": fingerprint}
            if request.get("fingerprint") != fingerprint:
                raise OrchestratorError(
                    "Session context changed; preview and confirm the message again"
                )
            if self.engine.profile.mode == "connected" and not self.connected_enabled:
                raise OrchestratorError("This dashboard has not enabled connected workers")
            self.engine.approve_session_turn(
                key, message, self.engine.profile.display_name, fingerprint
            )
            self.engine.launch_session_turn(key, message, connected_enabled=self.connected_enabled)
            return {"started": True}
        if action in {"preview-pause", "pause"}:
            preview = self.pause_preview(key)
            fingerprint = digest(preview)
            if action == "preview-pause":
                return {"preview": preview, "fingerprint": fingerprint}
            if request.get("fingerprint") != fingerprint:
                raise OrchestratorError("Worker changed; preview pause again")
            store = self.engine.store
            store.grant(
                store.request("pause-session", key, fingerprint), self.engine.profile.display_name
            )
            store.require("pause-session", key, fingerprint, consume=True)
            await self.engine.cancel(key, pause=True)
            return {"paused": True}
        raise OrchestratorError("Unknown conversation action")

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.handlers.add(task)
        try:
            try:
                async with asyncio.timeout(10):
                    raw = await reader.readline()
                    if not raw or len(raw) >= MAX_REQUEST:
                        raise OrchestratorError("Conversation request is too large or empty")
                    request = json.loads(raw)
                    if not isinstance(request, dict):
                        raise OrchestratorError("Invalid conversation request")
                    response = {"ok": True, "data": await self.dispatch(request)}
            except (ValueError, OSError, OrchestratorError, TimeoutError) as exc:
                response = {"ok": False, "error": str(exc)}
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            self.handlers.discard(task)


class SessionRemote:
    def __init__(self, descriptor: Path):
        self.descriptor = descriptor

    async def request(self, action: str, key: str, **values: Any) -> dict[str, Any]:
        writer: asyncio.StreamWriter | None = None
        try:
            if self.descriptor.is_symlink():
                raise OrchestratorError("Refusing a symlink session connection")
            stat = self.descriptor.stat()
            if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
                raise OrchestratorError("Session connection must be private to your account")
            connection = json.loads(self.descriptor.read_text())
            async with asyncio.timeout(12):
                reader, writer = await asyncio.open_unix_connection(
                    connection["socket"], limit=4 * 1024 * 1024
                )
                payload = {**values, "action": action, "key": key, "token": connection["token"]}
                writer.write((json.dumps(payload) + "\n").encode())
                await writer.drain()
                response = json.loads(await reader.readline())
            if not response["ok"]:
                raise OrchestratorError(response["error"])
            return dict(response["data"])
        except (OSError, ValueError, KeyError, TimeoutError) as exc:
            raise OrchestratorError(
                "The dashboard conversation connection is unavailable. Start or reopen the dashboard, then load this session again"
            ) from exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass


async def open_vscode(engine: Engine, key: str, bridge: SessionBridge) -> Path:
    if bridge.server is None:
        raise OrchestratorError(
            "The conversation bridge is unavailable; reopen the dashboard first"
        )
    snapshot = engine.session_snapshot(key)
    executable = shutil.which("code")
    if executable is None and platform.system() == "Darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            candidate = root / "Visual Studio Code.app/Contents/Resources/app/bin/code"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                executable = str(candidate)
                break
    if executable is None:
        raise OrchestratorError(
            "VS Code CLI 'code' was not found. In VS Code run 'Shell Command: Install code command in PATH', then restart this terminal"
        )
    descriptor = base64.urlsafe_b64encode(os.fsencode(bridge.descriptor)).decode()
    workspace = engine.runtime / "editors" / f"{key}.code-workspace"
    document = {
        "folders": [{"name": f"{key} [{snapshot['branch']}]", "path": snapshot["worktree"]}],
        "settings": {"window.title": f"{key} · {snapshot['branch']} — Gemini Sprint Orchestrator"},
        "tasks": {
            "version": "2.0.0",
            "tasks": [
                {
                    "label": f"Gemini session: {key}",
                    "type": "process",
                    "command": sys.executable,
                    "args": [
                        "-I",
                        "-m",
                        "gemini_orchestrator.session_ui",
                        "--connection",
                        descriptor,
                        "--ticket",
                        key,
                    ],
                    "options": {"cwd": snapshot["worktree"]},
                    "problemMatcher": [],
                    "runOptions": {"runOn": "folderOpen", "instanceLimit": 1},
                    "presentation": {"reveal": "always", "focus": True, "panel": "dedicated"},
                }
            ],
        },
    }
    atomic_write(workspace, json.dumps(document, indent=2))
    # Revalidate immediately before opening; do not check out or switch any branch.
    engine.validate_worktree(engine.store.row(key))
    await asyncio.to_thread(command, [executable, "--new-window", str(workspace)], timeout=15)
    engine.store.event("Opened recorded worktree in VS Code with the shared session terminal", key)
    return workspace
