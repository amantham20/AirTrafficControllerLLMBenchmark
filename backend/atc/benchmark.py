"""Benchmark harness: run scenarios with a controller, store every run in
SQLite, and produce scorecards and cross-run comparison reports.

    # one scenario, live LLM
    python -m atc.benchmark run --scenario baseline --model claude-sonnet-4-6

    # the full 5-scenario suite
    python -m atc.benchmark suite --model claude-sonnet-4-6

    # scripted / null baselines (no API key needed)
    python -m atc.benchmark run --scenario scripted_demo --controller scripted

    # reports
    python -m atc.benchmark list
    python -m atc.benchmark compare --scenario baseline
    python -m atc.benchmark scorecard --label claude-sonnet-4-6
"""

from __future__ import annotations

import argparse
from typing import Optional

from .airport import Airport
from .controllers import Controller, NullController, ScriptedController
from .engine import SimEngine
from .run_scripted import load_script
from .scenario import SCENARIO_DIR, list_scenarios, load_scenario
from .scoring import compute_metrics, delay_report, format_delay_report, \
    incident_log
from .storage import RunStore

SUITE = ["baseline", "rush_hour", "runway_change", "degraded", "emergency"]
SNAPSHOT_INTERVAL_S = 2


def build_controller(args) -> tuple[Controller, Optional[str]]:
    if args.controller == "llm":
        from .llm.adapter import LLMController
        return LLMController(model=args.model,
                             thinking=args.thinking), args.model
    if args.controller == "scripted":
        path = args.script or (SCENARIO_DIR / f"{args.scenario}_script.json")
        return load_script(path), None
    return NullController(), None


def run_one(store: RunStore, scenario_id: str, args) -> str:
    scenario = load_scenario(scenario_id)
    if args.seed is not None:
        scenario.seed = args.seed
    args.scenario = scenario_id
    controller, model = build_controller(args)
    airport = Airport.load(scenario.airport)
    sim = SimEngine(airport, scenario, controller=controller)

    print(f"--- {scenario.id} (seed {scenario.seed}, "
          f"{args.controller}{f':{model}' if model else ''}) ---")
    snapshots = []
    while sim.t < scenario.duration_s:
        sim.step()
        if sim.t % SNAPSHOT_INTERVAL_S == 0:
            snapshots.append(sim.ui_snapshot())

    print(format_delay_report(delay_report(sim)))
    incidents = incident_log(sim)
    if incidents:
        print(f"incidents: {len(incidents)}")

    llm_calls = getattr(controller, "call_log", [])
    label = args.label or model or args.controller
    run_id = store.save_run(sim, controller.name, model=model, label=label,
                            llm_calls=llm_calls, snapshots=snapshots)
    print(f"stored run {run_id}\n")
    return run_id


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

COMPARE_COLUMNS = [
    ("efficiency_score", "score", "{:.3f}"),
    ("total_delay_min", "delay(min)", "{:.1f}"),
    ("on_time_performance", "OTP", "{}"),
    ("throughput_ops_per_hour", "ops/h", "{}"),
    ("incidents", "incidents", "{}"),
    ("rejections", "rejections", "{}"),
    ("llm_latency_p95_ms", "p95(ms)", "{}"),
]


def format_comparison(rows: list[dict], title: str) -> str:
    lines = [f"=== {title} ===",
             f"{'scenario':<14} {'label':<26} {'score':>7} {'delay':>7} "
             f"{'OTP':>6} {'ops/h':>6} {'inc(m/M/c)':>11} {'rej':>5} "
             f"{'p95ms':>8} {'done':>5}"]
    for r in rows:
        inc = r.get("incidents") or {}
        rej = r.get("rejections") or {}
        inc_s = f"{inc.get('minor', 0)}/{inc.get('major', 0)}/" \
                f"{inc.get('critical', 0)}"
        rej_n = sum(rej.values()) if rej else 0
        lines.append(
            f"{r['scenario']:<14} {str(r['label'])[:26]:<26} "
            f"{r['efficiency_score'] if r['efficiency_score'] is not None else '-':>7} "
            f"{r['total_delay_min'] if r['total_delay_min'] is not None else '-':>7} "
            f"{r['on_time_performance'] if r['on_time_performance'] is not None else '-':>6} "
            f"{r['throughput_ops_per_hour'] if r['throughput_ops_per_hour'] is not None else '-':>6} "
            f"{inc_s:>11} {rej_n:>5} "
            f"{r['llm_latency_p95_ms'] if r['llm_latency_p95_ms'] is not None else '-':>8} "
            f"{'yes' if r['completed'] else 'NO':>5}")
    return "\n".join(lines)


def aggregate_scorecard(rows: list[dict]) -> str:
    """Per-label aggregate across scenarios."""
    by_label: dict[str, list[dict]] = {}
    for r in rows:
        by_label.setdefault(str(r["label"]), []).append(r)
    lines = ["=== Aggregate scorecard (mean across runs) ===",
             f"{'label':<26} {'runs':>5} {'score':>7} {'delay':>8} "
             f"{'critical':>9} {'completed':>10}"]
    for label, runs in sorted(by_label.items()):
        scores = [r["efficiency_score"] for r in runs
                  if r["efficiency_score"] is not None]
        delays = [r["total_delay_min"] for r in runs
                  if r["total_delay_min"] is not None]
        criticals = sum((r.get("incidents") or {}).get("critical", 0)
                        for r in runs)
        done = sum(1 for r in runs if r["completed"])
        lines.append(
            f"{label[:26]:<26} {len(runs):>5} "
            f"{sum(scores) / len(scores):>7.3f} "
            f"{sum(delays) / len(delays):>8.1f} "
            f"{criticals:>9} {f'{done}/{len(runs)}':>10}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_run_args(p):
        p.add_argument("--controller", choices=["llm", "scripted", "null"],
                       default="llm")
        p.add_argument("--model", default="claude-sonnet-4-6")
        p.add_argument("--thinking", choices=["adaptive"], default=None)
        p.add_argument("--script", default=None)
        p.add_argument("--seed", type=int, default=None)
        p.add_argument("--label", default=None,
                       help="label for comparisons (defaults to model)")
        p.add_argument("--db", default="runs.sqlite3")

    p_run = sub.add_parser("run", help="run one scenario")
    p_run.add_argument("--scenario", required=True)
    add_run_args(p_run)

    p_suite = sub.add_parser("suite", help="run the 5-scenario suite")
    p_suite.add_argument("--scenarios", default=",".join(SUITE))
    add_run_args(p_suite)

    p_list = sub.add_parser("list", help="list stored runs")
    p_list.add_argument("--db", default="runs.sqlite3")
    p_list.add_argument("--scenario", default=None)

    p_cmp = sub.add_parser("compare", help="compare runs of one scenario")
    p_cmp.add_argument("--db", default="runs.sqlite3")
    p_cmp.add_argument("--scenario", default=None)

    p_score = sub.add_parser("scorecard",
                             help="aggregate scorecard across scenarios")
    p_score.add_argument("--db", default="runs.sqlite3")
    p_score.add_argument("--label", default=None)

    p_scen = sub.add_parser("scenarios", help="list available scenarios")

    args = parser.parse_args()

    if args.cmd == "scenarios":
        for s in list_scenarios():
            print(f"{s['id']:<16} {s['aircraft']:>2} aircraft  "
                  f"{s['duration_s']:>5}s  {s['name']}")
        return

    store = RunStore(args.db)
    if args.cmd == "run":
        run_one(store, args.scenario, args)
    elif args.cmd == "suite":
        for scenario_id in args.scenarios.split(","):
            run_one(store, scenario_id.strip(), args)
        print(format_comparison(store.list_runs(), "Suite results"))
        print()
        print(aggregate_scorecard(store.list_runs()))
    elif args.cmd == "list":
        rows = store.list_runs(args.scenario)
        print(format_comparison(rows, "Stored runs"))
    elif args.cmd == "compare":
        rows = store.list_runs(args.scenario)
        title = f"Comparison — {args.scenario or 'all scenarios'}"
        print(format_comparison(rows, title))
        print()
        print(aggregate_scorecard(rows))
    elif args.cmd == "scorecard":
        rows = store.list_runs()
        if args.label:
            rows = [r for r in rows if str(r["label"]) == args.label]
        print(aggregate_scorecard(rows))


if __name__ == "__main__":
    main()
