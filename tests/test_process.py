from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from gemini_orchestrator.process import Supervisor


async def test_timeout_and_real_exit_code(tmp_path: Path) -> None:
    supervisor = Supervisor()
    result = await supervisor.run(
        "timeout",
        [sys.executable, "-c", "import time; time.sleep(30)"],
        tmp_path,
        0.2,
        tmp_path / "timeout.log",
    )
    assert result["timeout"]
    assert not result["passed"]
    failed = await supervisor.run(
        "failure",
        [sys.executable, "-c", "raise SystemExit(7)"],
        tmp_path,
        5,
        tmp_path / "failure.log",
    )
    assert failed["exit_code"] == 7
    assert not supervisor.running


async def test_cancellation_and_log_capture(tmp_path: Path) -> None:
    supervisor = Supervisor()
    started = asyncio.Event()
    task = asyncio.create_task(
        supervisor.run(
            "cancel",
            [sys.executable, "-u", "-c", "import time; print('running'); time.sleep(30)"],
            tmp_path,
            60,
            tmp_path / "cancel.log",
            on_start=lambda *_: started.set(),
        )
    )
    await started.wait()
    await asyncio.sleep(0.2)
    task.cancel()
    result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert not supervisor.running
    assert "running" in (tmp_path / "cancel.log").read_text()


async def test_guardian_terminates_children_when_coordinator_crashes(tmp_path: Path) -> None:
    import os
    import signal
    import subprocess

    marker = tmp_path / "escaped.txt"
    guardian_pid_file = tmp_path / "guardian.pid"
    script = f"""import asyncio
from pathlib import Path
from gemini_orchestrator.process import Supervisor
async def main():
    await Supervisor().run("child", [{sys.executable!r}, "-c", "import time,pathlib; time.sleep(2); pathlib.Path({str(marker)!r}).write_text('escaped')"], Path({str(tmp_path)!r}), 30, Path({str(tmp_path / "child.log")!r}), lambda pid, token: Path({str(guardian_pid_file)!r}).write_text(str(pid)))
asyncio.run(main())
"""
    coordinator = await asyncio.to_thread(
        subprocess.Popen,
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            if guardian_pid_file.exists():
                break
            await asyncio.sleep(0.01)
        assert guardian_pid_file.exists()
        coordinator.kill()
        await asyncio.to_thread(coordinator.wait, 5)
        await asyncio.sleep(2.3)
        assert not marker.exists()
    finally:
        if coordinator.poll() is None:
            coordinator.kill()
            coordinator.wait()
        if guardian_pid_file.exists():
            try:
                os.killpg(int(guardian_pid_file.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass


async def test_timeout_covers_a_child_that_never_reads_prompt(tmp_path: Path) -> None:
    supervisor = Supervisor()
    result = await supervisor.run(
        "blocked-input",
        [sys.executable, "-c", "import time; time.sleep(30)"],
        tmp_path,
        0.25,
        tmp_path / "blocked-input.log",
        stdin="x" * (4 * 1024 * 1024),
    )
    assert result["timeout"]
    assert not result["passed"]
    assert result["duration"] < 5
    assert not supervisor.running


async def test_callback_failure_stops_the_owned_child(tmp_path: Path) -> None:
    import pytest

    supervisor = Supervisor()

    def fail(*_):
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        await supervisor.run(
            "callback",
            [sys.executable, "-c", "import time; time.sleep(30)"],
            tmp_path,
            5,
            tmp_path / "callback.log",
            on_start=fail,
        )
    assert not supervisor.running


async def test_excessive_output_is_bounded_and_fails(tmp_path: Path) -> None:
    supervisor = Supervisor(max_log_bytes=4096)
    log = tmp_path / "noisy.log"
    result = await supervisor.run(
        "noisy",
        [sys.executable, "-c", "import sys; sys.stdout.write('x'*1000000); sys.stdout.flush()"],
        tmp_path,
        10,
        log,
    )
    assert result["output_limited"]
    assert not result["passed"]
    assert log.stat().st_size == 4096
    assert not supervisor.running
