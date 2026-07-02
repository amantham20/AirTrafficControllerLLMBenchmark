"""Scenario library, storage round-trip and comparison report tests."""

import json

from atc.airport import Airport
from atc.engine import SimEngine
from atc.benchmark import SUITE, aggregate_scorecard, format_comparison
from atc.run_scripted import load_script
from atc.scenario import SCENARIO_DIR, list_scenarios, load_scenario
from atc.storage import RunStore


def test_scenario_library_loads_and_validates():
    ids = {s["id"] for s in list_scenarios()}
    for sid in SUITE:
        assert sid in ids
        sc = load_scenario(sid)
        assert sc.traffic, sid
        # Gates and runways must exist on the airport.
        airport = Airport.load(sc.airport)
        for t in sc.traffic:
            assert t.gate in airport.gates, f"{sid}: bad gate {t.gate}"
            assert airport.runway_for_end(t.runway) is not None
        for ev in sc.events:
            assert ev.at_s < sc.duration_s


def test_suite_scenarios_have_expected_injections():
    assert any(e.kind == "runway_change"
               for e in load_scenario("runway_change").events)
    degraded = load_scenario("degraded")
    assert any(e.kind == "runway_closure" for e in degraded.events)
    assert degraded.pilot_error_rates.readback_error > 0.05
    emergency = load_scenario("emergency")
    ev = next(e for e in emergency.events if e.kind == "emergency")
    assert ev.params["callsign"] in {t.callsign for t in emergency.traffic}


def run_demo_engine():
    scenario = load_scenario("scripted_demo")
    controller = load_script(SCENARIO_DIR / "scripted_demo_script.json")
    sim = SimEngine(Airport.load(scenario.airport), scenario,
                    controller=controller)
    snapshots = []
    while sim.t < scenario.duration_s:
        sim.step()
        if sim.t % 5 == 0:
            snapshots.append(sim.ui_snapshot())
    return sim, snapshots


def test_storage_round_trip(tmp_path):
    sim, snapshots = run_demo_engine()
    store = RunStore(str(tmp_path / "test.sqlite3"))
    run_id = store.save_run(sim, "scripted", model=None, label="scripted",
                            snapshots=snapshots)

    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0]["id"] == run_id
    assert runs[0]["completed"] is True
    assert runs[0]["efficiency_score"] > 0.5

    detail = store.get_run(run_id)
    assert detail["report"]["scenario"] == "scripted_demo"
    assert detail["metrics"]["completed_takeoffs"] == 2

    events = store.get_events(run_id)
    assert len(events) == len(sim.events)
    assert events[0]["seq"] == 1
    json.dumps(events)

    stored_snaps = store.get_snapshots(run_id)
    assert len(stored_snaps) == len(snapshots)
    assert stored_snaps[-1]["t_s"] == snapshots[-1]["t_s"]
    # Replayable: every snapshot has aircraft positions.
    assert all("aircraft" in s for s in stored_snaps)


def test_comparison_report_renders(tmp_path):
    sim, _ = run_demo_engine()
    store = RunStore(str(tmp_path / "test.sqlite3"))
    store.save_run(sim, "scripted", label="run-a")
    store.save_run(sim, "scripted", label="run-b")
    rows = store.list_runs("scripted_demo")
    text = format_comparison(rows, "test")
    assert "run-a" in text and "run-b" in text
    agg = aggregate_scorecard(rows)
    assert "run-a" in agg and "2 more" not in agg
