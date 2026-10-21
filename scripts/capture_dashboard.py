"""Capture synthetic terminal documentation at wide and compact sizes."""

import asyncio
import tempfile
from pathlib import Path

from gemini_orchestrator.config import Profiles
from gemini_orchestrator.dashboard import Dashboard
from gemini_orchestrator.demo import create_demo
from gemini_orchestrator.engine import Engine


async def capture():
    with tempfile.TemporaryDirectory(prefix="gso-capture-") as directory:
        profiles = Profiles(Path(directory))
        profile = create_demo(profiles)
        profile.checks[0].argv[0] = "python3"  # Portable command in documentation captures.
        engine = Engine(profile, profiles.runtime(profile.name))
        await engine.discover()
        for width, height in [(120, 40), (80, 24)]:
            app = Dashboard(engine, profiles)
            async with app.run_test(size=(width, height)) as pilot:
                await pilot.pause()
                app.save_screenshot(f"dashboard-{width}.svg", path="docs")
                engine.select(["DEMO-1", "DEMO-2", "DEMO-3"], "Demo Human")
                app.action_plan()
                await pilot.pause()
                for selector in ("#approve-plan", "#launch", "#plan-text", "#planner", "#notice"):
                    widget = app.query_one(selector)
                    print(width, selector, widget.region, widget.visible, widget.display)
                app.save_screenshot(f"planner-{width}.svg", path="docs")
                print("click:", await pilot.click("#approve-plan"))
                await pilot.pause()
                print("screen:", app.screen, "notice:", app.query_one("#notice").render())
                await app.action_quit()
        engine.close()


asyncio.run(capture())
for capture_path in Path("docs").glob("*.svg"):
    capture_path.write_text(
        "\n".join(line.rstrip() for line in capture_path.read_text().splitlines()) + "\n"
    )
