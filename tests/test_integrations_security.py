from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from gemini_orchestrator.engine import Engine
from gemini_orchestrator.gemini import Gemini
from gemini_orchestrator.hosting import Hosting, perform_draft, perform_push, remote_fingerprint
from gemini_orchestrator.jira import JiraConnection, MCPTransport
from gemini_orchestrator.models import OrchestratorError


@pytest.mark.parametrize("provider,cli", [("github", "gh"), ("gitlab", "glab")])
def test_hosting_read_and_draft_arguments(tmp_path: Path, provider: str, cli: str) -> None:
    calls: list[list[str]] = []

    def fake(argv: list[str], *args: Any, **kwargs: Any) -> str:
        calls.append(argv)
        return "[]" if "list" in argv else "https://example.invalid/request/1"

    body = tmp_path / "description.md"
    body.write_text("Ticket summary\n\nLiteral `code` and $(not-executed)\n")
    with patch("gemini_orchestrator.hosting.command", side_effect=fake):
        adapter = Hosting(provider, tmp_path)
        assert adapter.read_requests() == []
        assert adapter.create_draft("gso/task", "main", "DEMO-1: Work", body).startswith("https://")
    assert all(call[0] == cli for call in calls)
    assert "--draft" in calls[1]
    assert "--body-file" in calls[1] if provider == "github" else body.read_text() in calls[1]


async def accepted(engine: Engine) -> None:
    await engine.discover()
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute()
    engine.accept("DEMO-1", "Alex")


async def test_remote_defaults_off_and_separate_exact_gates(engine: Engine) -> None:
    await accepted(engine)
    with pytest.raises(OrchestratorError, match="disabled"):
        remote_fingerprint(engine, "DEMO-1", "push")
    engine.profile.hosting = "github"
    engine.profile.remote_writes = True
    from gemini_orchestrator.git import Git

    git = Git(engine.profile.repository)
    git.run("remote", "add", "origin", "https://example.invalid/synthetic.git")
    fingerprint = remote_fingerprint(engine, "DEMO-1", "push")
    with pytest.raises(OrchestratorError, match="approval"):
        perform_push(engine, "DEMO-1")
    engine.store.grant(engine.store.request("push", "DEMO-1", fingerprint), "Alex")
    with patch.object(
        engine,
        "validate_worktree",
        return_value=__import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(),
    ):
        # Validate the mutation boundary without invoking any network command.
        with patch("gemini_orchestrator.hosting.remote_fingerprint", return_value=fingerprint):
            perform_push(engine, "DEMO-1")
    with pytest.raises(OrchestratorError, match="draft"):
        perform_draft(engine, "DEMO-1", "main")
    with pytest.raises(OrchestratorError):
        engine.store.require("push", "DEMO-1", fingerprint)


async def test_approved_commit_edit_blocks_push(engine: Engine) -> None:
    await accepted(engine)
    engine.profile.hosting, engine.profile.remote_writes = "github", True
    path = Path(engine.store.row("DEMO-1")["worktree"])
    (path / "new.txt").write_text("unreviewed")
    with pytest.raises(OrchestratorError, match="changed"):
        remote_fingerprint(engine, "DEMO-1", "push")


async def test_demo_cannot_connect_enterprise_adapters(engine: Engine) -> None:
    with pytest.raises(OrchestratorError, match="Demo"):
        async with JiraConnection(engine.profile):
            pass


async def test_connected_discovery_requires_opt_in(engine: Engine) -> None:
    engine.profile.mode = "connected"
    with pytest.raises(OrchestratorError, match="enable-connected"):
        await engine.discover()


async def test_mcp_disabled_in_gemini_settings(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {"mcpServers": {"jira": {"command": "do-not-execute"}}, "mcp": {"excluded": ["jira"]}}
        )
    )
    engine.profile.jira.settings_path = path
    engine.profile.jira.server = "jira"
    with pytest.raises(OrchestratorError, match="deny"):
        await MCPTransport(engine.profile).connect()


async def test_mcp_managed_oauth_fails_closed(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"mcpServers": {"jira": {"httpUrl": "https://example.invalid", "oauth": True}}})
    )
    engine.profile.jira.settings_path = path
    engine.profile.jira.server = "jira"
    with pytest.raises(OrchestratorError, match="OAuth"):
        await MCPTransport(engine.profile).connect()


def test_gemini_capabilities_inspected_not_assumed() -> None:
    with patch(
        "gemini_orchestrator.gemini.command",
        side_effect=[
            "0.99.0",
            "--prompt --output-format --admin-policy --resume",
            "list add remove",
        ],
    ):
        result = Gemini("gemini").inspect()
    assert result["prompt"] and result["admin_policy"] and result["mcp_list"]


def test_gemini_without_restriction_capability_disabled(engine: Engine) -> None:
    with patch.object(
        Gemini, "inspect", return_value={"prompt": True, "json": True, "admin_policy": False}
    ):
        with pytest.raises(OrchestratorError, match="admin-policy"):
            Gemini().worker(
                engine.profile,
                engine.store.ticket("DEMO-1")
                if engine.store.rows()
                else __import__(
                    "gemini_orchestrator.demo", fromlist=["demo_tickets"]
                ).demo_tickets()[0],
                engine.profile.repository,
                engine.runtime,
                [],
            )


def test_gemini_worker_policy_deny_shell_and_mcp(engine: Engine) -> None:
    from gemini_orchestrator.demo import demo_tickets

    with (
        patch.object(
            Gemini, "inspect", return_value={"prompt": True, "json": True, "admin_policy": True}
        ),
        patch("gemini_orchestrator.gemini.platform.system", return_value="Darwin"),
        patch("gemini_orchestrator.gemini.shutil.which", return_value="/usr/bin/sandbox-exec"),
    ):
        argv, prompt = Gemini().worker(
            engine.profile,
            demo_tickets()[0],
            engine.profile.repository,
            engine.runtime,
            ["human review"],
        )
    assert argv[0] == "sandbox-exec"
    policy = (engine.runtime / "worker-policy.toml").read_text()
    assert 'toolName = "*"' in policy and 'decision = "deny"' in policy
    assert "run_shell_command" not in policy
    assert "mcp" not in policy
    assert "human review" in prompt
    assert "untrusted" in prompt
    assert "--yolo" not in argv


def test_child_process_no_shell_interpolation(tmp_path: Path) -> None:
    import sys

    from gemini_orchestrator.git import command

    marker = tmp_path / "should-not-exist"
    literal = f"$(touch {marker})"
    assert command([sys.executable, "-c", "import sys; print(sys.argv[1])", literal]) == literal
    assert not marker.exists()


def test_os_sandbox_enforces_write_boundary(engine: Engine, tmp_path: Path) -> None:
    import platform
    import shutil
    import subprocess
    import sys

    from gemini_orchestrator.demo import demo_tickets

    if platform.system() != "Darwin" or not shutil.which("sandbox-exec"):
        pytest.skip(
            "macOS sandbox-exec boundary test; Linux bubblewrap configuration is mocked separately"
        )
    with patch.object(
        Gemini, "inspect", return_value={"prompt": True, "json": True, "admin_policy": True}
    ):
        argv, _ = Gemini().worker(
            engine.profile, demo_tickets()[0], engine.profile.repository, engine.runtime, []
        )
    allowed = engine.profile.repository / "allowed.txt"
    denied = tmp_path / "outside.txt"
    script = (
        "from pathlib import Path; Path("
        + repr(str(allowed))
        + ").write_text('allowed'); Path("
        + repr(str(denied))
        + ").write_text('denied')"
    )
    result = subprocess.run(
        [*argv[:3], sys.executable, "-c", script], capture_output=True, text=True
    )
    if "sandbox_apply: Operation not permitted" in result.stderr:
        pytest.skip(
            "Host sandbox cannot nest sandbox-exec; run this test with approved local process access"
        )
    assert result.returncode != 0
    assert allowed.read_text() == "allowed"
    assert not denied.exists()


async def test_real_connected_worker_with_synthetic_cli(engine: Engine, tmp_path: Path) -> None:
    import platform
    import shutil
    import sys

    if not shutil.which("sandbox-exec" if platform.system() == "Darwin" else "bwrap"):
        pytest.skip("A supported OS sandbox is required for connected workers")
    executable = tmp_path / "synthetic-gemini"
    executable.write_text(f"""#!{sys.executable}
import sys,json,pathlib
if "--version" in sys.argv:
    print("synthetic-1.0")
elif "--help" in sys.argv:
    print("list" if "mcp" in sys.argv else "--prompt --output-format --admin-policy")
else:
    payload = json.loads(sys.stdin.read())
    assert payload["ticket_data_untrusted"]["key"] == "DEMO-1"
    pathlib.Path("implementation.txt").write_text("Synthetic CLI implementation")
    print(json.dumps({{"response": "Implemented synthetic task; no enterprise access"}}))
""")
    executable.chmod(0o700)
    await engine.discover()
    engine.profile.mode = "connected"
    engine.profile.gemini.executable = str(executable)
    engine.profile.gemini.retries = 0
    engine.select(["DEMO-1"], "Alex")
    engine.propose()
    engine.approve_plan("Alex")
    engine.approve_launch("Alex")
    await engine.execute(connected_enabled=True)
    row = engine.store.row("DEMO-1")
    result = json.loads(row["result"] or "{}")
    if (
        row["state"] == "FAILED"
        and result.get("log")
        and "sandbox_apply: Operation not permitted"
        in await asyncio.to_thread(Path(result["log"]).read_text)
    ):
        pytest.skip("Host sandbox cannot nest sandbox-exec")
    assert row["state"] == "READY_FOR_REVIEW"
    assert result["summary"].startswith("Implemented synthetic")
    assert (
        Path(row["worktree"]) / "implementation.txt"
    ).read_text() == "Synthetic CLI implementation"
