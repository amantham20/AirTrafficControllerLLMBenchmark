"""End-to-end scripted scenario: the Phase 1 deliverable, plus determinism."""

import json

from atc.airport import Airport
from atc.engine import SimEngine
from atc.run_scripted import load_script
from atc.scenario import SCENARIO_DIR, load_scenario
from atc.scoring import compute_metrics, delay_report, incident_log


def run_demo(seed=None, error_rates=None):
    scenario = load_scenario("scripted_demo")
    if seed is not None:
        scenario.seed = seed
    if error_rates is not None:
        scenario.pilot_error_rates = error_rates
    controller = load_script(SCENARIO_DIR / "scripted_demo_script.json")
    engine = SimEngine(Airport.load(scenario.airport), scenario,
                       controller=controller)
    engine.run()
    return engine


def test_scripted_demo_completes_cleanly():
    engine = run_demo()
    assert engine.all_complete
    m = compute_metrics(engine)
    assert m["completed_takeoffs"] == 2
    assert m["completed_landings"] == 1
    assert m["incidents"] == {"minor": 0, "major": 0, "critical": 0}
    assert m["rejections"] == {"minor": 0, "major": 0, "critical": 0}
    assert m["on_time_performance"] == 1.0
    assert m["avg_taxi_ratio"] is not None and m["avg_taxi_ratio"] < 2.0
    assert 0.5 < m["efficiency_score"] <= 1.0
    assert incident_log(engine) == []

    report = delay_report(engine)
    assert len(report["aircraft"]) == 3
    # Both departures had to wait at the 14/32 crossing: RUNWAY_QUEUE > 0.
    by_cs = {a["callsign"]: a for a in report["aircraft"]}
    assert by_cs["UAL245"]["delay_by_cause"].get("RUNWAY_QUEUE", 0) > 0


def test_deterministic_same_seed():
    """Same seed + same script = byte-identical event log."""
    from atc.scenario import PilotErrorRatesModel
    rates = PilotErrorRatesModel(readback_error=0.15, stuck_mic=0.10,
                                 slow_response=0.10)
    a = run_demo(seed=123, error_rates=rates)
    b = run_demo(seed=123, error_rates=rates)
    log_a = [(e.t_s, e.type.value, e.callsign, e.text) for e in a.events]
    log_b = [(e.t_s, e.type.value, e.callsign, e.text) for e in b.events]
    assert log_a == log_b
    assert compute_metrics(a)["total_delay_min"] == \
        compute_metrics(b)["total_delay_min"]


def test_ui_snapshot_serializable():
    engine = run_demo()
    snap = engine.ui_snapshot()
    json.dumps(snap)
    assert snap["t_s"] == engine.t
    assert len(snap["aircraft"]) == 3


def test_controller_view_serializable():
    engine = run_demo()
    view = engine.controller_view()
    json.dumps(view)
    assert "runways" in view and "05/23" in view["runways"]
