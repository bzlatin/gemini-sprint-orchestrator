from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gemini_orchestrator.demo import demo_tickets
from gemini_orchestrator.engine import Engine
from gemini_orchestrator.models import OrchestratorError
from gemini_orchestrator.planning import create_plan
from gemini_orchestrator.semantic import recommendations


@pytest.mark.parametrize("kind", ["AI-inferred", "potential conflict", "confirmed"])
async def test_semantic_results_cannot_become_authoritative(engine: Engine, kind: str) -> None:
    tickets = demo_tickets()[:2]
    plan = create_plan(engine.profile, tickets)

    async def fake_run(*args, **kwargs):
        log: Path = args[4]
        log.parent.mkdir(exist_ok=True)
        await asyncio.to_thread(
            log.write_text,
            json.dumps(
                {
                    "response": json.dumps(
                        {
                            "edges": [
                                {
                                    "prerequisite": "DEMO-1",
                                    "dependent": "DEMO-2",
                                    "kind": kind,
                                    "reason": "Potential shared module; verify manually",
                                }
                            ]
                        }
                    )
                }
            ),
        )
        assert "--yolo" not in args[1]
        return {"passed": True}

    with (
        patch(
            "gemini_orchestrator.semantic.Gemini.inspect",
            return_value={"prompt": True, "json": True, "admin_policy": True},
        ),
        patch("gemini_orchestrator.semantic.Gemini.assert_supplemental_policy"),
        patch("gemini_orchestrator.semantic.Supervisor.run", side_effect=fake_run),
    ):
        if kind == "confirmed":
            with pytest.raises(OrchestratorError, match="invalid advisory"):
                await recommendations(
                    engine.profile, plan, [t.model_dump() for t in tickets], engine.runtime
                )
        else:
            edges = await recommendations(
                engine.profile, plan, [t.model_dump() for t in tickets], engine.runtime
            )
            assert edges[0].kind == kind
            assert plan.groups == [["DEMO-1", "DEMO-2"]]
    assert 'decision = "deny"' in (engine.runtime / "analysis-policy.toml").read_text()


async def test_semantic_requires_connected_opt_in(engine: Engine) -> None:
    with pytest.raises(OrchestratorError, match="explicit connected"):
        await engine.propose_semantic()
