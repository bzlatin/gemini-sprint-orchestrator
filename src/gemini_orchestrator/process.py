from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from .models import OrchestratorError


class Supervisor:
    def __init__(self) -> None:
        self.running: dict[str, asyncio.subprocess.Process] = {}

    async def run(
        self,
        key: str,
        argv: list[str],
        cwd: Path,
        timeout_seconds: float,
        log: Path,
        on_start: Callable[[int, str], None] | None = None,
        stdin: str | None = None,
    ) -> dict[str, object]:
        if key in self.running:
            raise OrchestratorError(f"{key} already has a process")
        token = uuid.uuid4().hex
        runner = Path(__file__).with_name("runner.py")
        started = time.monotonic()
        log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        # Redirect to disk rather than buffering unbounded worker output in memory.
        with os.fdopen(fd, "wb") as output:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(runner),
                str(os.getpid()),
                token,
                *argv,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
            self.running[key] = proc
            if on_start:
                on_start(proc.pid, token)
            if stdin is not None and proc.stdin:
                proc.stdin.write(stdin.encode())
                await proc.stdin.drain()
                proc.stdin.close()
            timed_out = False
            try:
                await asyncio.wait_for(proc.wait(), timeout_seconds)
            except TimeoutError:
                timed_out = True
                await self.stop(key)
            except asyncio.CancelledError:
                await self.stop(key)
                raise
            finally:
                self.running.pop(key, None)
        return {
            "argv": argv,
            "exit_code": proc.returncode,
            "duration": round(time.monotonic() - started, 3),
            "timeout": timed_out,
            "log": str(log),
            "passed": proc.returncode == 0 and not timed_out,
        }

    async def stop(self, key: str) -> None:
        proc = self.running.get(key)
        if proc and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                await asyncio.wait_for(proc.wait(), 3)
            except TimeoutError:
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
            except ProcessLookupError:
                pass

    async def shutdown(self) -> None:
        await asyncio.gather(*(self.stop(key) for key in list(self.running)))
