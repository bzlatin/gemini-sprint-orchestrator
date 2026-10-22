"""Fail-closed process isolation for generated code and model workers."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from .config import atomic_write, private_dir
from .models import OrchestratorError, Profile

SYSTEM_READS = (
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/System",
    "/Library/Apple",
    "/dev",
    "/private/etc",
    "/etc/ssl",
    "/etc/ld.so.cache",
    "/etc/localtime",
    "/etc/resolv.conf",
    "/etc/hosts",
    "/etc/nsswitch.conf",
)
EXECUTION_CONFIGS = (".vscode", ".gemini", ".agents", ".claude", ".env", ".env.local")


def scratch(runtime: Path, key: str, purpose: str = "worker") -> Path:
    root = private_dir(runtime / key)
    return private_dir(root / purpose)


def environment(home: Path, *, model: bool = False) -> dict[str, str]:
    """No inherited tokens, agents, preload hooks, Python/Node flags or proxy credentials."""
    temporary = private_dir(home / "tmp")
    paths = ["/usr/bin", "/bin", "/usr/sbin", "/sbin", str(Path(sys.executable).parent)]
    for tool in ("node", "gemini", "python3", "git", "npm"):
        found = shutil.which(tool)
        if found:
            paths.append(str(Path(found).parent))
    result = {
        "PATH": os.pathsep.join(dict.fromkeys(paths)),
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "LANG": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "CI": "1",
    }
    if model:
        for name in (
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_GENAI_USE_VERTEXAI",
        ):
            if name in os.environ:
                result[name] = os.environ[name]
    return result


@lru_cache(maxsize=32)
def dynamic_libraries(executable: str) -> tuple[Path, ...]:
    if platform.system() != "Darwin" or not Path("/usr/bin/otool").is_file():
        return ()
    pending = [Path(executable), Path(sys.executable).resolve()]
    for pattern in ("_ssl*", "_hashlib*", "_bz2*", "_lzma*", "_sqlite3*", "_ctypes*"):
        pending.extend(
            (
                Path(sys.base_prefix)
                / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/lib-dynload"
            ).glob(pattern + ".so")
        )
    visited: set[Path] = set()
    libraries: set[Path] = set()
    while pending and len(visited) < 128:
        binary = pending.pop().resolve()
        if binary in visited:
            continue
        visited.add(binary)
        result = subprocess.run(
            ["/usr/bin/otool", "-L", str(binary)], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines()[1:]:
            path = line.strip().split(" (", 1)[0]
            if path.startswith("/") and Path(path).is_file():
                library = Path(path).resolve()
                if library not in libraries:
                    libraries.add(library)
                    pending.append(library)
    return tuple(sorted(libraries))


def resolve_executable(value: str) -> str:
    return sys.executable if value in {"python", "python3"} else (shutil.which(value) or value)


def tooling(argv: list[str], extra: list[Path]) -> list[Path]:
    found = resolve_executable(argv[0])
    executable = Path(found).resolve()
    if not executable.is_file():
        raise OrchestratorError(f"Executable is unavailable: {argv[0]}")
    reads = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), executable]
    # A global npm installation needs its package dependencies, not the user's home.
    for candidate in (executable, Path(shutil.which("node") or "/nonexistent").resolve()):
        if "node_modules" in candidate.parts:
            i = candidate.parts.index("node_modules")
            reads.append(Path(*candidate.parts[: i + 1]))
        elif candidate.name == "node" and candidate.is_file():
            reads.append(candidate.parent.parent)
    reads.extend(dynamic_libraries(str(executable)))
    reads.extend(p.expanduser().resolve() for p in extra)
    for p in reads:
        if p in {
            Path("/"),
            Path.home(),
            Path("/Users"),
            Path("/home"),
            Path("/tmp"),
            Path("/private/tmp"),
        }:
            raise OrchestratorError("Tool read paths cannot expose a home or temporary root")
    return list(dict.fromkeys(reads))


def assert_editor_safe(worktree: Path) -> None:
    # These settings can execute code when an editor trusts the generated checkout.
    names = [".vscode", ".gemini", ".agents", ".claude"]
    if any(
        (worktree / name).is_symlink()
        or (
            (worktree / name).exists()
            and (not (worktree / name).is_dir() or any((worktree / name).iterdir()))
        )
        for name in names
    ) or any(worktree.glob("*.code-workspace")):
        raise OrchestratorError(
            "Editor opening blocked: this worktree contains agent/editor execution configuration. Review and remove it before opening the shared-session workspace"
        )


def wrap(
    profile: Profile,
    argv: list[str],
    worktree: Path,
    runtime: Path,
    key: str,
    *,
    worker: bool = False,
    read_only: bool = False,
) -> tuple[list[str], dict[str, str]]:
    worktree = worktree.resolve()
    purpose = "ask" if read_only else "worker" if worker else "verification"
    home = scratch(runtime, key, purpose)
    env = environment(home, model=worker)
    reads = [Path(p) for p in SYSTEM_READS if Path(p).exists()]
    reads += tooling(argv, profile.isolation.tool_read_paths)
    reads += [worktree, home]
    # Grant no ancestor of controller state or another worker's checkout through tooling.
    for path in reads:
        if path not in {worktree, home} and (
            runtime.resolve().is_relative_to(path)
            or profile.worktrees.resolve().is_relative_to(path)
            or (path.is_relative_to(runtime.resolve()) and not path.is_relative_to(home))
            or (
                path.is_relative_to(profile.worktrees.resolve())
                and not path.is_relative_to(worktree)
            )
        ):
            raise OrchestratorError(
                "Tool read path would expose controller state or other worktrees"
            )
    writes = [home] + ([] if read_only else [worktree])
    executable = resolve_executable(argv[0])
    argv = [str(Path(executable).resolve()), *argv[1:]]
    if worker:
        settings = runtime / "worker-settings.json"
        atomic_write(
            settings,
            json.dumps(
                {
                    "hooksConfig": {"enabled": False},
                    "advanced": {"ignoreLocalEnv": True},
                    "skills": {"enabled": False},
                    "mcpServers": {},
                    "security": {"enablePermanentToolApproval": False},
                    "telemetry": {"enabled": False},
                }
            ),
        )
        env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(settings)
        env["GEMINI_CLI_SYSTEM_DEFAULTS_PATH"] = str(settings)
        reads.append(settings)
        if "--admin-policy" in argv:
            policy_file = Path(argv[argv.index("--admin-policy") + 1]).resolve()
            if policy_file.parent != runtime.resolve():
                raise OrchestratorError("Model policy must belong to the coordinator")
            reads.append(policy_file)
    system = platform.system()
    if system == "Darwin" and shutil.which("sandbox-exec"):

        def clauses(paths: list[Path]) -> str:
            return " ".join(f"(subpath {json.dumps(str(p.resolve()))})" for p in paths)

        policy = '(version 1)\n(allow default)\n(deny file-read* file-write*)\n(allow file-read-metadata)\n(allow file-read-data (literal "/"))\n'
        policy += f'(allow file-read* {clauses(reads)})\n(allow file-write* {clauses(writes)} (literal "/dev/null"))\n'
        if not worker:
            policy += "(deny network*)\n(deny mach-lookup)\n"
        for name in (".vscode", ".gemini", ".agents", ".claude"):
            policy += f"(deny file-write* (subpath {json.dumps(str(worktree / name))}))\n"
        # Config/hooks from generated code are never executable configuration for Gemini.
        if worker:
            for ancestor in [worktree, *worktree.parents]:
                for name in EXECUTION_CONFIGS:
                    policy += f"(deny file-read* (subpath {json.dumps(str(ancestor / name))}))\n"
        policy_path = runtime / f"{key}-{purpose}.sb"
        atomic_write(policy_path, policy)
        return ["sandbox-exec", "-f", str(policy_path), *argv], env
    if system == "Linux" and shutil.which("bwrap"):
        args = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL"]
        if worker:
            args += ["--share-net"]
        args += ["--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev"]
        mounted: set[str] = set()
        for p in reads:
            if str(p) in mounted or str(p) == "/dev" or not p.exists():
                continue
            args += ["--ro-bind", str(p.resolve()), str(p)]
            mounted.add(str(p))
        for p in writes:
            args += ["--bind", str(p), str(p)]
        empty = private_dir(runtime / "empty-config")
        for name in (".vscode", ".gemini", ".agents", ".claude"):
            p = worktree / name
            if p.is_symlink() or (p.exists() and not p.is_dir()):
                raise OrchestratorError("Refusing symlink or non-directory execution configuration")
            p.mkdir(exist_ok=True, mode=0o700)
            args += ["--ro-bind", str(empty), str(p)]
        if worker:
            # Mask existing project configuration. New mounts remain read-only to the worker.
            empty = private_dir(runtime / "empty-config")
            for ancestor in [worktree, *worktree.parents]:
                for name in EXECUTION_CONFIGS:
                    p = ancestor / name
                    if p.exists():
                        args += ["--ro-bind", str(empty) if p.is_dir() else "/dev/null", str(p)]
        return [*args, "--chdir", str(worktree), "--", *argv], env
    raise OrchestratorError(
        "Process isolation requires macOS sandbox-exec or Linux bubblewrap; no unsafe fallback"
    )
