from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import Button, Input, Static, TabbedContent, TextArea

from gemini_orchestrator.dashboard import Confirmation, Dashboard
from gemini_orchestrator.engine import Engine
from gemini_orchestrator.git import Git
from gemini_orchestrator.models import OrchestratorError, State, digest
from gemini_orchestrator.sessions import SessionBridge, SessionRemote, open_vscode


async def ready(engine: Engine, keys: list[str] | None = None) -> None:
    await engine.discover()
    engine.select(keys or ["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    assert engine.store.row("DEMO-1")["state"] == State.READY_FOR_REVIEW


async def test_followup_is_one_use_preserves_worktree_and_reverifies(engine: Engine) -> None:
    await ready(engine)
    row = engine.store.row("DEMO-1")
    old_verification = json.loads(row["verification"])
    old_log = json.loads(row["result"])["log"]
    engine.accept("DEMO-1", "Alex")
    message = "Add a clearer [example] with Unicode: café 日本語"
    preview = engine.session_preview("DEMO-1", message)
    with pytest.raises(OrchestratorError, match="approval"):
        engine.launch_session_turn("DEMO-1", message)
    engine.approve_session_turn("DEMO-1", message, "Alex", digest(preview))
    task = engine.launch_session_turn("DEMO-1", message)
    assert engine.store.row("DEMO-1")["state"] == State.QUEUED
    assert engine.store.row("DEMO-1")["verification"] is None
    await task
    after = engine.store.row("DEMO-1")
    assert after["state"] == State.READY_FOR_REVIEW
    assert after["worktree"] == row["worktree"] and after["branch"] == row["branch"]
    assert (
        json.loads(after["verification"])["results"][0]["log"]
        != old_verification["results"][0]["log"]
    )
    assert await asyncio.to_thread(Path(old_log).exists)
    assert "accepted_fingerprint" not in json.loads(after["result"])
    messages = engine.conversation("DEMO-1")
    assert [item["role"] for item in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[-2]["text"] == message
    assert message in messages[-1]["text"]
    with pytest.raises(OrchestratorError, match="approval"):
        engine.launch_session_turn("DEMO-1", message)
    assert not engine.executing and not engine.tasks


@pytest.mark.parametrize("change", ["tree", "message", "settings"])
async def test_stale_followup_preview_cannot_launch(engine: Engine, change: str) -> None:
    await ready(engine)
    preview = engine.session_preview("DEMO-1", "Explain the edge case")
    if change == "tree":
        (Path(preview["worktree"]) / "manual.txt").write_text("human edit after preview")
    elif change == "settings":
        engine.profile.gemini.timeout += 1
    message = "Different instruction" if change == "message" else preview["message"]
    with pytest.raises(OrchestratorError):
        engine.approve_session_turn("DEMO-1", message, "Alex", digest(preview))
    assert engine.store.row("DEMO-1")["state"] == State.READY_FOR_REVIEW
    assert len(engine.conversation("DEMO-1")) == 2


async def test_changed_worktree_after_approval_needs_new_confirmation(engine: Engine) -> None:
    await ready(engine)
    preview = engine.session_preview("DEMO-1", "Improve it")
    engine.approve_session_turn("DEMO-1", preview["message"], "Alex", digest(preview))
    (Path(preview["worktree"]) / "manual.txt").write_text("changed")
    with pytest.raises(OrchestratorError, match="approval"):
        engine.launch_session_turn("DEMO-1", preview["message"])


async def test_followup_invalidates_accepted_descendants(engine: Engine) -> None:
    await ready(engine, ["DEMO-1", "DEMO-3"])
    engine.accept("DEMO-1", "Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    engine.accept("DEMO-3", "Alex")
    preview = engine.session_preview("DEMO-1", "Revise the example")
    engine.approve_session_turn("DEMO-1", preview["message"], "Alex", digest(preview))
    task = engine.launch_session_turn("DEMO-1", preview["message"])
    assert engine.store.row("DEMO-3")["state"] == State.CHANGES_REQUESTED
    assert engine.store.row("DEMO-3")["verification"] is None
    await task


async def test_followup_needs_original_approved_scope_and_idle_coordinator(engine: Engine) -> None:
    await ready(engine)
    engine.executing = True
    with pytest.raises(OrchestratorError, match="batch"):
        engine.session_preview("DEMO-1", "Hello")
    engine.executing = False
    engine.store.db.execute("UPDATE approvals SET granted=0 WHERE action='plan'")
    with pytest.raises(OrchestratorError, match="approved scope"):
        engine.session_preview("DEMO-1", "Hello")
    assert len(engine.conversation("DEMO-1")) == 2


async def test_two_clients_share_one_session_and_server_enforces_preview(engine: Engine) -> None:
    await ready(engine)
    bridge = SessionBridge(engine)
    await bridge.start()
    try:
        one = SessionRemote(bridge.descriptor)
        two = SessionRemote(bridge.descriptor)
        first = await one.request("snapshot", "DEMO-1")
        assert first["branch"] == engine.store.row("DEMO-1")["branch"]
        with pytest.raises(OrchestratorError, match="expired"):
            await bridge.dispatch({"token": "invalid", "action": "snapshot", "key": "DEMO-1"})
        with pytest.raises(OrchestratorError, match="confirm"):
            await one.request("send", "DEMO-1", message="A new example", fingerprint="wrong")
        pending = await one.request("preview", "DEMO-1", message="A new example")
        await one.request(
            "send", "DEMO-1", message="A new example", fingerprint=pending["fingerprint"]
        )
        await asyncio.wait_for(asyncio.gather(*engine.tasks.values()), 10)
        second = await two.request("snapshot", "DEMO-1")
        assert second["worktree"] == first["worktree"]
        assert second["messages"][-2]["text"] == "A new example"
        assert len(second["messages"]) == len(first["messages"]) + 2
        with pytest.raises(OrchestratorError, match="confirm"):
            await two.request(
                "send", "DEMO-1", message="A new example", fingerprint=pending["fingerprint"]
            )
        assert bridge.descriptor.stat().st_mode & 0o777 == 0o600
    finally:
        await bridge.close()
    assert not bridge.descriptor.exists()
    with pytest.raises(OrchestratorError, match="unavailable"):
        await one.request("snapshot", "DEMO-1")


async def test_bridge_rejects_oversized_and_invalid_protocol(engine: Engine) -> None:
    bridge = SessionBridge(engine)
    await bridge.start()
    try:
        connection = json.loads(bridge.descriptor.read_text())
        reader, writer = await asyncio.open_unix_connection(connection["socket"])
        writer.write(b"[]\n")
        await writer.drain()
        response = json.loads(await reader.readline())
        assert response["ok"] is False
        assert "Invalid" in response["error"]
        writer.close()
        await writer.wait_closed()
        remote = SessionRemote(bridge.descriptor)
        bridge.descriptor.chmod(0o644)
        with pytest.raises(OrchestratorError, match="private"):
            await remote.request("snapshot", "DEMO-1")
    finally:
        bridge.descriptor.chmod(0o600)
        await bridge.close()


async def test_vscode_opens_exact_worktree_with_shared_terminal_and_no_repo_changes(
    engine: Engine,
) -> None:
    engine.profile.worktrees = engine.profile.repository.parent / "worktrees with spaces"
    await ready(engine)
    bridge = SessionBridge(engine)
    await bridge.start()
    try:
        row = engine.store.row("DEMO-1")
        git = Git(Path(row["worktree"]))
        before = git.fingerprint()
        calls = []
        with (
            patch("gemini_orchestrator.sessions.shutil.which", return_value="/fake path/code"),
            patch(
                "gemini_orchestrator.sessions.command",
                side_effect=lambda argv, **kw: calls.append(argv) or "",
            ),
        ):
            workspace = await open_vscode(engine, "DEMO-1", bridge)
        document = json.loads(workspace.read_text())
        assert document["folders"][0]["path"] == row["worktree"]
        assert row["branch"] in document["folders"][0]["name"]
        task = document["tasks"]["tasks"][0]
        assert task["type"] == "process" and task["options"]["cwd"] == row["worktree"]
        assert "gemini_orchestrator.session_ui" in task["args"]
        assert task["args"][0] == "-I"  # Do not import code from the editable worktree.
        assert "--resume" not in task["args"]  # The coordinator owns the Gemini process.
        assert calls == [["/fake path/code", "--new-window", str(workspace)]]
        assert git.fingerprint() == before
        assert not (git.root / ".vscode").exists()
        assert (
            workspace.is_relative_to(engine.runtime) and workspace.stat().st_mode & 0o777 == 0o600
        )
        git.run("-c", "core.hooksPath=/dev/null", "checkout", "-b", "gso/manual-switch")
        with pytest.raises(OrchestratorError, match="branch"):
            await open_vscode(engine, "DEMO-1", bridge)
    finally:
        await bridge.close()


async def test_dashboard_conversation_confirmation_drafts_and_compact_actions(
    engine: Engine, profiles
) -> None:
    await ready(engine)
    app = Dashboard(engine, profiles)
    async with app.run_test(size=(80, 24)) as pilot:
        app.load_session("DEMO-1")
        await pilot.pause()
        editor = app.query_one("#session-message", TextArea)
        editor.load_text("Please add [example] café")
        app.query_one("#session-send", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, Confirmation)
        app.screen.query_one("#back", Button).press()
        await pilot.pause()
        assert editor.text == "Please add [example] café"
        assert len(engine.conversation("DEMO-1")) == 2
        app.query_one("#session-send", Button).press()
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()
        async with asyncio.timeout(10):
            while engine.executing:
                await pilot.pause(0.1)
        app.update_views(force=True)
        assert "Please add [example] café" in str(
            app.query_one("#session-history", Static).render()
        )
        assert not editor.text
        assert app.query_one("#session-send", Button).region.bottom <= app.size.height - 1
        assert app.query_one("#load-session", Button).region.right <= app.size.width - 1
        assert app.query_one(TabbedContent).active == "sessions"
        app.query_one("#session-key", Input).value = "DEMO-2"
        with pytest.raises(OrchestratorError, match="Load"):
            app.checked_session_key()
        await app.action_quit()


async def test_remote_console_confirmation(engine: Engine) -> None:
    from gemini_orchestrator.session_ui import SessionConsole

    await ready(engine)
    bridge = SessionBridge(engine)
    await bridge.start()
    try:
        app = SessionConsole(bridge.descriptor, "DEMO-1")
        async with app.run_test(size=(80, 24)) as pilot:
            assert not app.query("#session-key")
            assert "DEMO-1" in str(app.query_one("#session-fixed-key", Static).render())
            async with asyncio.timeout(5):
                while app.query_one("#session-send", Button).disabled:
                    await pilot.pause(0.1)
            app.query_one("#session-message", TextArea).load_text("From the editor terminal")
            app.query_one("#session-send", Button).press()
            async with asyncio.timeout(5):
                while not isinstance(app.screen, Confirmation) or not app.screen.query("#confirm"):
                    await pilot.pause(0.1)
            app.screen.query_one("#confirm", Button).press()
            await pilot.pause(0.2)
            async with asyncio.timeout(10):
                while engine.executing:
                    await pilot.pause(0.1)
            app.refresh_session()
            await pilot.pause(0.2)
            assert "From the editor terminal" in str(
                app.query_one("#session-history", Static).render()
            )
            assert not app.query_one("#session-message", TextArea).text
            assert app.query_one("#session-send", Button).region.bottom <= 23
    finally:
        await bridge.close()


async def test_native_gemini_session_id_is_resumed_exactly(engine: Engine, tmp_path: Path) -> None:
    import platform
    import shutil
    import sys

    if not shutil.which("sandbox-exec" if platform.system() == "Darwin" else "bwrap"):
        pytest.skip("A supported OS sandbox is required for a real synthetic connected worker")
    session_id = "5edd3346-7384-4adc-a755-3775a4f17abb"
    executable = tmp_path / "synthetic-gemini"
    executable.write_text(f"""#!{sys.executable}
import json,sys,pathlib
if '--version' in sys.argv:
    print('synthetic-session-1')
elif '--help' in sys.argv:
    print('list' if 'mcp' in sys.argv else '--prompt --output-format --admin-policy --resume')
else:
    data=json.loads(sys.stdin.read())
    path=pathlib.Path('session-turn.json')
    resumed='--resume' in sys.argv
    if resumed:
        assert sys.argv[sys.argv.index('--resume')+1] == {session_id!r}
        assert path.exists()
        assert data['conversation'][-1]['text'] == 'Continue with a real resumed turn'
    else:
        assert not path.exists()
    path.write_text(json.dumps({{'resumed':resumed}}))
    print(json.dumps({{'response':'Resumed same native session' if resumed else 'Initial native session','session_id':{session_id!r}}}))
""")
    executable.chmod(0o700)
    engine.profile.mode = "connected"
    engine.profile.gemini.executable = str(executable)
    engine.profile.gemini.retries = 0
    # Keep discovery synthetic while exercising the real connected execution path.
    from gemini_orchestrator.demo import demo_tickets

    engine.store.upsert(demo_tickets())
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute(connected_enabled=True)
    row = engine.store.row("DEMO-1")
    result = json.loads(row["result"] or "{}")
    if (
        row["state"] == State.FAILED
        and result.get("log")
        and "sandbox_apply: Operation not permitted"
        in await asyncio.to_thread(Path(result["log"]).read_text)
    ):
        pytest.skip("Host sandbox cannot nest sandbox-exec")
    assert row["state"] == State.READY_FOR_REVIEW, engine.store.events()
    assert engine.worker_session_id("DEMO-1") == session_id
    message = "Continue with a real resumed turn"
    preview = engine.session_preview("DEMO-1", message)
    engine.approve_session_turn("DEMO-1", message, "Alex", digest(preview))
    with pytest.raises(OrchestratorError, match="enable-connected"):
        engine.launch_session_turn("DEMO-1", message)
    await engine.launch_session_turn("DEMO-1", message, connected_enabled=True)
    assert engine.store.row("DEMO-1")["state"] == State.READY_FOR_REVIEW, engine.store.events()
    assert engine.worker_session_id("DEMO-1") == session_id
    assert json.loads((Path(row["worktree"]) / "session-turn.json").read_text())["resumed"] is True
    assert engine.conversation("DEMO-1")[-1]["text"] == "Resumed same native session"


async def test_pausing_one_worker_keeps_other_workers_supervised(engine: Engine) -> None:
    import sys

    await engine.discover()
    engine.select(["DEMO-1", "DEMO-2"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    started = asyncio.Event()
    count = 0
    original = engine.supervisor.run

    async def slow_worker(key, argv, cwd, worker_deadline, log, on_start=None, stdin=None):
        if ":verify" in key:
            return await original(key, argv, cwd, worker_deadline, log, on_start, stdin)

        def running(pid, token):
            nonlocal count
            if on_start:
                on_start(pid, token)
            count += 1
            if count == 2:
                started.set()

        return await original(
            key,
            [sys.executable, "-c", "import time; time.sleep(1)"],
            cwd,
            worker_deadline,
            log,
            running,
        )

    with patch.object(engine.supervisor, "run", side_effect=slow_worker):
        batch = asyncio.create_task(engine.execute())
        await asyncio.wait_for(started.wait(), 5)
        await engine.cancel("DEMO-1", pause=True)
        await asyncio.wait_for(batch, 10)
    assert engine.store.row("DEMO-1")["state"] == State.PAUSED
    assert engine.store.row("DEMO-2")["state"] == State.READY_FOR_REVIEW
    assert not engine.tasks and not engine.executing and not engine.supervisor.running
    assert engine.session_preview("DEMO-1", "Continue this paused worker")["key"] == "DEMO-1"


async def test_message_editor_keeps_global_shortcuts_as_text(engine: Engine, profiles) -> None:
    await ready(engine)
    app = Dashboard(engine, profiles)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert await pilot.click("#--content-tab-agents")
        app.query_one("#agent-key", Input).value = "DEMO-1"
        assert await pilot.click("#agent-session")
        await pilot.pause(0.2)
        editor = app.query_one("#session-message", TextArea)
        assert app.focused is editor
        assert app.query_one(TabbedContent).active == "sessions"
        original_plan = engine.store.plan().id
        selection = set(app.selected)
        await pilot.press("p", "q", "r", "space", "x")
        assert editor.text == "pqr x"
        assert engine.store.plan().id == original_plan
        assert app.selected == selection
        await app.action_quit()


async def test_worker_startup_failure_is_visible_in_shared_history(engine: Engine) -> None:
    await ready(engine)
    message = "Continue this task"
    preview = engine.session_preview("DEMO-1", message)
    engine.approve_session_turn("DEMO-1", message, "Alex", digest(preview))
    with patch.object(
        engine.supervisor, "run", side_effect=OSError("Synthetic process startup failed")
    ):
        await engine.launch_session_turn("DEMO-1", message)
    snapshot = engine.session_snapshot("DEMO-1")
    assert snapshot["state"] == State.FAILED
    assert snapshot["messages"][-1]["role"] == "system"
    assert snapshot["messages"][-1]["text"] == "Synthetic process startup failed"
    assert not engine.executing and not engine.tasks


async def test_editor_client_can_pause_verification_without_losing_work(engine: Engine) -> None:
    import sys

    from gemini_orchestrator.models import Check

    await engine.discover()
    engine.profile.checks = [
        Check(
            name="Synthetic long verification",
            argv=[sys.executable, "-c", "import time; time.sleep(5)"],
        )
    ]
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    started = asyncio.Event()
    original = engine.supervisor.run

    async def observed(key, argv, cwd, worker_deadline, log, on_start=None, stdin=None):
        def running(pid, token):
            if on_start:
                on_start(pid, token)
            if ":verify" in key:
                started.set()

        return await original(key, argv, cwd, worker_deadline, log, running, stdin)

    bridge = SessionBridge(engine)
    await bridge.start()
    try:
        with patch.object(engine.supervisor, "run", side_effect=observed):
            batch = asyncio.create_task(engine.execute())
            await asyncio.wait_for(started.wait(), 10)
            before = engine.session_snapshot("DEMO-1")
            assert before["can_pause"] and before["state"] == State.VERIFYING
            remote = SessionRemote(bridge.descriptor)
            preview = await remote.request("preview-pause", "DEMO-1")
            await remote.request("pause", "DEMO-1", fingerprint=preview["fingerprint"])
            await asyncio.wait_for(batch, 10)
        after = engine.session_snapshot("DEMO-1")
        assert after["state"] == State.PAUSED
        assert after["worktree"] == before["worktree"]
        assert not engine.supervisor.running and not engine.tasks
    finally:
        await bridge.close()


async def test_editor_client_cannot_import_a_worker_created_shadow_package(engine: Engine) -> None:
    import subprocess
    import sys

    await ready(engine)
    root = Path(engine.store.row("DEMO-1")["worktree"])
    shadow = root / "gemini_orchestrator"
    shadow.mkdir()
    marker = root / "shadow-imported.txt"
    (shadow / "__init__.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('unsafe import')\n"
    )
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-I", "-m", "gemini_orchestrator.session_ui", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--connection" in result.stdout
    assert not await asyncio.to_thread(marker.exists)
