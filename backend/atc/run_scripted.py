"""Headless scripted run — the Phase 1 deliverable.

    python -m atc.run_scripted --scenario scripted_demo \
        [--script atc/scenarios/scripted_demo_script.json] [--json]

Runs the simulation with a fixed instruction script (no LLM) and prints the
delay report and incident log.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .airport import Airport
from .controllers import ScriptedController
from .engine import SimEngine
from .scenario import SCENARIO_DIR, load_scenario
from .scoring import delay_report, format_delay_report, incident_log


def load_script(path: Path) -> ScriptedController:
    with open(path) as f:
        raw = json.load(f)
    entries = []
    for item in raw["script"]:
        at_s = item.pop("at_s")
        entries.append((at_s, item))
    return ScriptedController(entries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="scripted_demo")
    parser.add_argument("--script", default=None,
                        help="path to a script JSON; defaults to "
                        "<scenario>_script.json next to the scenario")
    parser.add_argument("--json", action="store_true",
                        help="emit the full report as JSON")
    args = parser.parse_args()

    scenario = load_scenario(args.scenario)
    script_path = Path(args.script) if args.script else \
        SCENARIO_DIR / f"{args.scenario}_script.json"
    controller = load_script(script_path)

    airport = Airport.load(scenario.airport)
    engine = SimEngine(airport, scenario, controller=controller)
    engine.run()

    report = delay_report(engine)
    incidents = incident_log(engine)
    if args.json:
        print(json.dumps({"report": report, "incidents": incidents},
                         indent=2))
        return
    print(format_delay_report(report))
    print()
    if incidents:
        print(f"=== Incidents ({len(incidents)}) ===")
        for inc in incidents:
            print(f"  t={inc['t_s']:>5}s [{inc['severity']}] "
                  f"{inc['type']}: {inc['text']}")
    else:
        print("=== No incidents ===")
    print()
    print(f"complete: {engine.all_complete}")


if __name__ == "__main__":
    main()
