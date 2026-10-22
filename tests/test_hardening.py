from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from gemini_orchestrator.engine import Engine
from gemini_orchestrator.isolation import assert_editor_safe, environment, wrap
from gemini_orchestrator.models import OrchestratorError, digest
from gemini_orchestrator.process import Supervisor
from gemini_orchestrator.sessions import MAX_REQUEST, SessionBridge, SessionRemote
from gemini_orchestrator.storage import old_logs, usage


def isolated(engine: Engine, script: str, *, worker: bool = False, read_only: bool = False) -> dict:
    tool = "sandbox-exec" if platform.system() == "Darwin" else "bwrap"
    if not shutil.which(tool):
        pytest.skip("Supported OS sandbox is unavailable")
    argv, env = wrap(
        engine.profile,
        [sys.executable, "-c", script],
        engine.profile.repository,
        engine.runtime,
        "DEMO-1",
        worker=worker,
        read_only=read_only,
    )
    result = subprocess.run(
        argv, env=env, cwd=engine.profile.repository, capture_output=True, text=True, timeout=10
    )
    if "sandbox_apply: Operation not permitted" in result.stderr:
        pytest.skip("Host sandbox cannot nest sandbox-exec")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_verification_cannot_read_controller_or_send_network(
    engine: Engine, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("JIRA_TOKEN", "synthetic-canary")
    canary = engine.runtime / "controller-canary.txt"
    canary.write_text("synthetic private data")
    link = engine.profile.repository / "escape"
    link.symlink_to(canary)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = isolated(
            engine,
            f"""import json,os,socket
from pathlib import Path
out={{}}
for name,path in [('controller',{str(canary)!r}),('symlink',{str(link)!r})]:
    try: Path(path).read_text(); out[name]=False
    except (PermissionError,FileNotFoundError): out[name]=True
try:
    socket.create_connection(('127.0.0.1',{port}),timeout=.2); out['network']=False
except OSError: out['network']=True
out['environment']='JIRA_TOKEN' not in os.environ
Path('allowed-check-output.txt').write_text('allowed')
print(json.dumps(out))
""",
        )
    assert result == {"controller": True, "symlink": True, "network": True, "environment": True}


def test_worker_cannot_read_bridge_or_other_worktree(engine: Engine, tmp_path: Path) -> None:
    bridge = engine.runtime / "conversation-bridge.json"
    bridge.write_text('{"token":"synthetic"}')
    other = tmp_path / "other-worktree"
    other.mkdir()
    (other / "canary").write_text("synthetic private work")
    config = engine.profile.repository / ".gemini"
    config.mkdir()
    (config / "settings.json").write_text('{"hooks":{"SessionStart":[]}}')
    script = f"""import json
from pathlib import Path
out={{}}
for name,path in [('bridge',{str(bridge)!r}),('other',{str(other / "canary")!r}),('config',{str(config / "settings.json")!r})]:
    try: Path(path).read_text(); out[name]=False
    except (PermissionError,FileNotFoundError): out[name]=True
try:
    Path('.vscode').mkdir(exist_ok=True); Path('.vscode/tasks.json').write_text('unsafe'); out['config_write']=False
except OSError: out['config_write']=True
print(json.dumps(out))
"""
    assert isolated(engine, script, worker=True) == {
        "bridge": True,
        "other": True,
        "config": True,
        "config_write": True,
    }


def test_read_only_question_has_os_write_denial(engine: Engine) -> None:
    result = isolated(
        engine,
        """import json
from pathlib import Path
try: Path('should-not-exist').write_text('bad'); denied=False
except OSError: denied=True
print(json.dumps({'denied':denied}))
""",
        worker=True,
        read_only=True,
    )
    assert result == {"denied": True}
    assert not (engine.profile.repository / "should-not-exist").exists()


@pytest.mark.parametrize(
    "name", [".vscode", ".gemini", ".agents", ".claude", "unsafe.code-workspace"]
)
def test_editor_rejects_execution_configs_and_symlinks(tmp_path: Path, name: str) -> None:
    (tmp_path / name).symlink_to(tmp_path / "missing")
    with pytest.raises(OrchestratorError, match="execution configuration"):
        assert_editor_safe(tmp_path)


def test_minimal_environment_and_no_unsafe_fallback(engine: Engine, monkeypatch) -> None:
    for key in (
        "JIRA_TOKEN",
        "GITHUB_TOKEN",
        "SSH_AUTH_SOCK",
        "NODE_OPTIONS",
        "PYTHONPATH",
        "GEMINI_API_KEY",
    ):
        monkeypatch.setenv(key, "synthetic")
    env = environment(engine.runtime / "clean")
    assert not any(
        k in env
        for k in (
            "JIRA_TOKEN",
            "GITHUB_TOKEN",
            "SSH_AUTH_SOCK",
            "NODE_OPTIONS",
            "PYTHONPATH",
            "GEMINI_API_KEY",
        )
    )
    assert (
        environment(engine.runtime / "model", model=True)["GEMINI_API_KEY"]
        == os.environ["GEMINI_API_KEY"]
    )
    with patch("gemini_orchestrator.isolation.platform.system", return_value="Unsupported"):
        with pytest.raises(OrchestratorError, match="no unsafe fallback"):
            wrap(
                engine.profile,
                [sys.executable, "-c", "pass"],
                engine.profile.repository,
                engine.runtime,
                "DEMO-1",
            )


async def test_ticket_scoped_bridge_and_oversized_request(engine: Engine) -> None:
    await engine.discover()
    bridge = SessionBridge(engine)
    await bridge.start()
    try:
        path = bridge.descriptor_for("DEMO-1")
        remote = SessionRemote(path)
        with pytest.raises(OrchestratorError, match="another ticket"):
            await remote.request("snapshot", "DEMO-2")
        connection = json.loads(path.read_text())
        reader, writer = await asyncio.open_unix_connection(connection["socket"])
        writer.write(b"x" * (MAX_REQUEST + 10) + b"\n")
        await writer.drain()
        response = json.loads(await reader.readline())
        assert not response["ok"]
        writer.close()
        await writer.wait_closed()
    finally:
        await bridge.close()
    assert not path.exists()


async def test_read_only_question_preserves_acceptance_and_needs_approval(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    engine.accept("DEMO-1", "Alex")
    before = dict(engine.store.row("DEMO-1"))
    question = "Explain the implementation"
    with pytest.raises(OrchestratorError, match="approval"):
        await engine.ask("DEMO-1", question)
    preview = engine.session_preview("DEMO-1", question)
    engine.store.grant(engine.store.request("ask", "DEMO-1", digest(preview)), "Alex")
    assert "read-only" in await engine.ask("DEMO-1", question)
    after = dict(engine.store.row("DEMO-1"))
    assert after == before
    with pytest.raises(OrchestratorError, match="approval"):
        await engine.ask("DEMO-1", question)


async def test_storage_limit_stops_process_and_retention_preserves_current(tmp_path: Path) -> None:
    supervisor = Supervisor()
    supervisor.storage_limits = (1024, 100)
    result = await supervisor.run(
        "large",
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import time; Path('large').write_bytes(b'x'*2048); time.sleep(10)",
        ],
        tmp_path,
        15,
        tmp_path / "current.log",
    )
    assert not result["passed"] and result["storage_error"]
    assert not supervisor.running
    with pytest.raises(OrchestratorError, match="Storage"):
        usage(tmp_path, 1024, 100)
    logs = tmp_path / "logs"
    logs.mkdir()
    for name in ("old.log", "current.log"):
        (logs / name).write_text("synthetic")
        os.utime(logs / name, (time.time() - 40 * 86400,) * 2)
    (logs / "symlink.log").symlink_to(tmp_path / "large")
    assert [r["name"] for r in old_logs(tmp_path, 30, {str(logs / "current.log")})] == ["old.log"]


async def test_connected_read_only_question_uses_separate_policy(
    engine: Engine, tmp_path: Path
) -> None:
    from gemini_orchestrator.demo import demo_tickets

    if not shutil.which("sandbox-exec" if platform.system() == "Darwin" else "bwrap"):
        pytest.skip("Supported OS sandbox is unavailable")
    executable = tmp_path / "synthetic-question-cli"
    executable.write_text(f"""#!{sys.executable}
import sys,json
from pathlib import Path
if '--version' in sys.argv: print('synthetic-question-1')
elif '--help' in sys.argv: print('list --prompt --output-format --admin-policy --resume')
else:
    context=json.loads(sys.stdin.read())
    policy=Path(sys.argv[sys.argv.index('--admin-policy')+1])
    if policy.name=='analysis-policy.toml':
        assert 'allow' not in policy.read_text()
        print(json.dumps({{'response':json.dumps({{'edges':[]}})}}))
    elif policy.name=='question-policy.toml':
        assert 'write_file' not in policy.read_text() and 'replace' not in policy.read_text()
        assert '--resume' not in sys.argv
        try: Path('question-modified-source').write_text('bad'); raise AssertionError('write allowed')
        except OSError: pass
        print(json.dumps({{'response':'Read-only synthetic answer'}}))
    else:
        Path('implementation.txt').write_text('synthetic implementation')
        print(json.dumps({{'response':'Implemented'}}))
""")
    executable.chmod(0o700)
    engine.profile.mode = "connected"
    engine.profile.gemini.executable = str(executable)
    engine.profile.gemini.retries = 0
    engine.store.upsert(demo_tickets())
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute(connected_enabled=True)
    question = "Explain the implementation"
    before = dict(engine.store.row("DEMO-1"))
    preview = engine.session_preview("DEMO-1", question)
    engine.store.grant(engine.store.request("ask", "DEMO-1", digest(preview)), "Alex")
    assert (
        await engine.ask("DEMO-1", question, connected_enabled=True) == "Read-only synthetic answer"
    )
    assert dict(engine.store.row("DEMO-1")) == before
    from gemini_orchestrator.planning import create_plan
    from gemini_orchestrator.semantic import recommendations

    tickets = demo_tickets()[:1]
    assert (
        await recommendations(
            engine.profile,
            create_plan(engine.profile, tickets),
            [t.model_dump() for t in tickets],
            engine.runtime,
        )
        == []
    )
    with pytest.raises(OrchestratorError, match="context changed"):
        engine.approve_session_turn("DEMO-1", question, "Alex", digest(preview))


async def test_editor_credentials_rotate_and_reconnect(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    first = SessionBridge(engine)
    await first.start()
    path = first.descriptor_for("DEMO-1")
    old = json.loads(path.read_text())["token"]
    await first.close()
    second = SessionBridge(engine)
    await second.start()
    try:
        assert path.exists()
        assert json.loads(path.read_text())["token"] != old
        assert (await SessionRemote(path).request("snapshot", "DEMO-1"))["key"] == "DEMO-1"
        with pytest.raises(OrchestratorError, match="expired"):
            await second.dispatch({"action": "snapshot", "key": "DEMO-1", "token": old})
    finally:
        await second.close()


def test_linux_mounts_exclude_host_root_and_network(engine: Engine) -> None:
    original = shutil.which

    def find(name: str) -> str | None:
        return "/usr/bin/bwrap" if name == "bwrap" else original(name)

    with (
        patch("gemini_orchestrator.isolation.platform.system", return_value="Linux"),
        patch("gemini_orchestrator.isolation.shutil.which", side_effect=find),
    ):
        argv, env = wrap(
            engine.profile,
            [sys.executable, "-c", "pass"],
            engine.profile.repository,
            engine.runtime,
            "DEMO-1",
        )
    assert "--unshare-all" in argv and "--share-net" not in argv
    assert not any(argv[i : i + 3] == ["--ro-bind", "/", "/"] for i in range(len(argv)))
    assert env["HOME"] != str(Path.home())
    assert str(engine.runtime / "conversation-bridge.json") not in argv


def test_additional_tool_paths_cannot_expose_controller(engine: Engine) -> None:
    engine.profile.isolation.tool_read_paths = [engine.runtime]
    with pytest.raises(OrchestratorError, match="expose controller"):
        wrap(
            engine.profile,
            [sys.executable, "-c", "pass"],
            engine.profile.repository,
            engine.runtime,
            "DEMO-1",
        )


def test_central_settings_are_never_silently_replaced() -> None:
    from gemini_orchestrator.gemini import Gemini

    original = Path.exists

    def exists(path: Path) -> bool:
        return str(path) == "/etc/gemini-cli/settings.json" or original(path)

    with (
        patch("gemini_orchestrator.gemini.platform.system", return_value="Linux"),
        patch.object(Path, "exists", exists),
    ):
        with pytest.raises(OrchestratorError, match="organization controls"):
            Gemini().assert_supplemental_policy()


def test_verification_cannot_read_host_process_environment(engine: Engine) -> None:
    host = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        env={**os.environ, "GSO_PRIVATE_CANARY": "synthetic-host-process-canary"},
    )
    try:
        result = isolated(
            engine,
            f"""import subprocess,json
try:
    r=subprocess.run(['/bin/ps','eww','-p',{str(host.pid)!r}],capture_output=True,text=True)
    visible='synthetic-host-process-canary' in r.stdout
except OSError: visible=False
print(json.dumps({{'host_environment_visible':visible}}))
""",
        )
        assert result == {"host_environment_visible": False}
    finally:
        host.terminate()
        host.wait(timeout=5)
