from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gemini_orchestrator.config import Profiles
from gemini_orchestrator.demo import create_demo
from gemini_orchestrator.engine import Engine


@pytest.fixture
def profiles(tmp_path: Path) -> Profiles:
    return Profiles(tmp_path / "gso")


@pytest.fixture
def engine(profiles: Profiles) -> Iterator[Engine]:
    profile = create_demo(profiles)
    # Most lifecycle tests exercise implementation directly; plan-first has dedicated tests.
    profile.gemini.plan_first = False
    engine = Engine(profile, profiles.runtime(profile.name))
    yield engine
    engine.close()
