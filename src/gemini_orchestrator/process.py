from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import IO

from .models import OrchestratorError


class Supervisor:
    def __init__(self, max_log_bytes: int = 8 * 1024 * 1024) -> None:
        self.running: dict[str, asyncio.subprocess.Process] = {}
        self.max_log_bytes = max_log_bytes
        self._starting: set[str] = set()
        self.storage_limits: tuple[int, int] | None = None
        self.storage_roots: dict[str, list[Path]] = {}

    async def run(
        self,
        key: str,
        argv: list[str],
        cwd: Path,
        timeout_seconds: float,
        log: Path,
        on_start: Callable[[int, str], None] | None = None,
        stdin: str | None = None,
        *,
        env: dict[str, str] | None = None,
    ) -> dict[str, object]:
        if key in self.running or key in self._starting:
            raise OrchestratorError(f"{key} already has a process")
        token = uuid.uuid4().hex
        runner = Path(__file__).with_name("runner.py")
        started = time.monotonic()
        log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        proc: asyncio.subprocess.Process | None = None
        timed_out = False
        output_limited = False
        input_error = ""
        storage_error = ""
        monitor: asyncio.Task[None] | None = None
        pump: asyncio.Task[bool] | None = None
        with os.fdopen(fd, "wb") as output:
            self._starting.add(key)
            try:
                # The deadline covers spawn, prompt delivery and output draining.
                async with asyncio.timeout(timeout_seconds):
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-I",
                        str(runner),
                        str(os.getpid()),
                        token,
                        *argv,
                        cwd=cwd,
                        env=env,
                        stdin=asyncio.subprocess.PIPE
                        if stdin is not None
                        else asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                    )
                    self.running[key] = proc
                    assert proc.stdout is not None
                    pump = asyncio.create_task(self._capture(key, proc.stdout, output))
                    if on_start:
                        on_start(proc.pid, token)
                    if stdin is not None and proc.stdin:
                        try:
                            proc.stdin.write(stdin.encode())
                            await proc.stdin.drain()
                            proc.stdin.close()
                            await proc.stdin.wait_closed()
                        except (BrokenPipeError, ConnectionResetError) as exc:
                            input_error = type(exc).__name__
                            await self.stop(key)
                    limits = self.storage_limits
                    if limits:

                        async def watch_storage() -> None:
                            nonlocal storage_error
                            from .storage import usage

                            while True:
                                try:
                                    for root in [cwd, *self.storage_roots.get(key, [])]:
                                        await asyncio.to_thread(usage, root, *limits)
                                except (OSError, OrchestratorError) as exc:
                                    storage_error = str(exc)
                                    await self.stop(key)
                                    return
                                await asyncio.sleep(0.5)

                        monitor = asyncio.create_task(watch_storage())
                    await proc.wait()
                    output_limited = await asyncio.shield(pump)
            except TimeoutError:
                timed_out = True
            finally:
                if monitor is not None:
                    monitor.cancel()
                    await asyncio.gather(monitor, return_exceptions=True)
                self._starting.discard(key)
                # Cleanup also runs on cancellation, callback failure and broken input pipes.
                if proc is not None:
                    await self.stop(key)
                if self.storage_limits:
                    from .storage import usage

                    try:
                        for root in [cwd, *self.storage_roots.pop(key, [])]:
                            await asyncio.to_thread(usage, root, *self.storage_limits)
                    except (OSError, OrchestratorError) as exc:
                        storage_error = str(exc)
                if pump is not None:
                    if not pump.done():
                        pump.cancel()
                    captured = await asyncio.gather(pump, return_exceptions=True)
                    output_limited = output_limited or captured[0] is True
                self.running.pop(key, None)
        return {
            "argv": argv,
            "exit_code": proc.returncode if proc is not None else None,
            "duration": round(time.monotonic() - started, 3),
            "timeout": timed_out,
            "output_limited": output_limited,
            "input_error": input_error,
            "storage_error": storage_error,
            "log": str(log),
            "passed": proc is not None
            and proc.returncode == 0
            and not timed_out
            and not output_limited
            and not input_error
            and not storage_error,
        }

    async def _capture(self, key: str, stream: asyncio.StreamReader, output: IO[bytes]) -> bool:
        size = 0
        limited = False
        stopper: asyncio.Task[None] | None = None
        while chunk := await stream.read(65536):
            remaining = self.max_log_bytes - size
            if remaining > 0:
                output.write(chunk[:remaining])
                output.flush()
                size += min(len(chunk), remaining)
            if len(chunk) > remaining and not limited:
                limited = True
                # Continue draining the pipe while termination runs, otherwise a
                # full StreamReader buffer can prevent subprocess.wait completing.
                stopper = asyncio.create_task(self.stop(key))
        if stopper is not None:
            await stopper
        return limited

    async def stop(self, key: str) -> None:
        proc = self.running.get(key)
        if proc is None:
            return
        # The guardian can exit before a descendant closes stdout. Its owned process
        # group must still be terminated; a saved PID from another session is never used.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except TimeoutError:
            pass
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()

    async def shutdown(self) -> None:
        await asyncio.gather(*(self.stop(key) for key in list(self.running)))
