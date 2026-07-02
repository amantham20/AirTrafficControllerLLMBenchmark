"""Headless LLM-controlled run — the Phase 2 deliverable.

    export ANTHROPIC_API_KEY=...
    python -m atc.run_llm --scenario baseline [--model claude-sonnet-4-6]
        [--out runs/baseline.json] [--thinking adaptive]

Runs a full scenario with a live LLM making every controller decision and
writes a complete run log (events, decisions, latency, metrics) plus the
delay report and incident log.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .airport import Airport
from .engine import SimEngine
from .llm.adapter import DEFAULT_MODEL, LLMController
from .scenario import load_scenario
from .scoring import compute_metrics, delay_report, format_delay_report, \
    incident_log


def run_log_dict(engine: SimEngine, controller: LLMController,
                 model: str) -> dict:
    return {
        "scenario": engine.scenario.id,
        "seed": engine.scenario.seed,
        "model": model,
        "controller": controller.name,
        "metrics": compute_metrics(engine),
        "report": delay_report(engine),
        "incidents": incident_log(engine),
        "decisions": engine.decision_log,
        "llm_calls": controller.call_log,
        "events": [e.model_dump(exclude_none=True) for e in engine.events],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="baseline")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--thinking", choices=["adaptive"], default=None)
    parser.add_argument("--out", default=None,
                        help="path for the JSON run log "
                        "(default runs/<scenario>_<model>.json)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("warning: ANTHROPIC_API_KEY not set; relying on ambient "
              "credentials")

    scenario = load_scenario(args.scenario)
    if args.seed is not None:
        scenario.seed = args.seed
    airport = Airport.load(scenario.airport)
    controller = LLMController(model=args.model, thinking=args.thinking)
    engine = SimEngine(airport, scenario, controller=controller)

    print(f"Running '{scenario.id}' ({scenario.duration_s}s sim) with "
          f"{args.model} ...")
    engine.run()

    report = delay_report(engine)
    print(format_delay_report(report))
    incidents = incident_log(engine)
    print()
    if incidents:
        print(f"=== Incidents ({len(incidents)}) ===")
        for inc in incidents:
            print(f"  t={inc['t_s']:>5}s [{inc['severity']}] "
                  f"{inc['type']}: {inc['text']}")
    else:
        print("=== No incidents ===")
    m = report["metrics"]
    print(f"\nLLM decisions: {m['decision_points']}   "
          f"latency avg {m['llm_latency_avg_ms']}ms / "
          f"p95 {m['llm_latency_p95_ms']}ms")
    print(f"complete: {engine.all_complete}")

    out = Path(args.out) if args.out else \
        Path("runs") / f"{scenario.id}_{args.model}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(run_log_dict(engine, controller, args.model), f, indent=1)
    print(f"run log written to {out}")


if __name__ == "__main__":
    main()
