"""Delay tracking and attribution tests."""

from atc.instructions import (
    ApprovePushback,
    CrossRunway,
    HoldPosition,
    TaxiInstruction,
)
from atc.models import AircraftState, DelayCause
from atc.scenario import WeatherState
from atc.scoring import compute_metrics, delay_report

from conftest import ROUTE_P2_TO_HS05, arr, dep, make_engine, step_until


def test_gate_hold_accrues_before_pushback_approval(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    for _ in range(100):
        engine.step()
    ac = engine.aircraft["UAL1"]
    hold = ac.delay_by_cause.get(DelayCause.GATE_HOLD.value, 0)
    assert 85 <= hold <= 95  # ticks 11..100
    engine.apply_instruction(ApprovePushback(callsign="UAL1"))
    engine.step()
    frozen = ac.delay_by_cause[DelayCause.GATE_HOLD.value]
    for _ in range(20):
        engine.step()
    # Pushing back now; GATE_HOLD stops accruing.
    assert ac.delay_by_cause[DelayCause.GATE_HOLD.value] == frozen


def test_runway_queue_at_hold_short(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ac = None

    def spawned():
        nonlocal ac
        ac = engine.aircraft.get("UAL1")
        return ac is not None and ac.pending_request == "pushback"
    step_until(engine, spawned, what="spawn")
    engine.apply_instruction(ApprovePushback(callsign="UAL1"))
    step_until(engine, lambda: ac.state == AircraftState.TAXI_OUT,
               what="ready")
    engine.apply_instruction(
        TaxiInstruction(callsign="UAL1", route=ROUTE_P2_TO_HS05))
    step_until(engine, lambda: ac.position.node == "HS_A_E" and
               ac.speed_ms == 0, what="waiting at crossing")
    before = ac.delay_by_cause.get(DelayCause.RUNWAY_QUEUE.value, 0)
    for _ in range(50):
        engine.step()
    after = ac.delay_by_cause.get(DelayCause.RUNWAY_QUEUE.value, 0)
    assert after - before >= 49


def test_congestion_behind_stopped_leader(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10),
                                   dep("DAL2", "G5", sched=20)])
    step_until(engine, lambda: len(engine.aircraft) == 2, what="spawn")
    ual, dal = engine.aircraft["UAL1"], engine.aircraft["DAL2"]
    step_until(engine, lambda: ual.pending_request == "pushback",
               what="request")
    engine.apply_instruction(ApprovePushback(callsign="UAL1"))
    engine.apply_instruction(ApprovePushback(callsign="DAL2"))
    step_until(engine, lambda: ual.state == AircraftState.TAXI_OUT and
               dal.state == AircraftState.TAXI_OUT, what="both ready")
    engine.apply_instruction(
        TaxiInstruction(callsign="UAL1", route=ROUTE_P2_TO_HS05))
    engine.apply_instruction(TaxiInstruction(
        callsign="DAL2",
        route=["P2"] + ROUTE_P2_TO_HS05))
    # UAL1 stops at the 14/32 crossing; DAL2 piles up behind it.
    step_until(engine, lambda: dal.speed_ms == 0 and
               dal.position.edge_b in ("HS_A_E", "A4") and
               engine._stopped_reason.get("DAL2") is not None,
               what="DAL2 blocked", max_ticks=600)
    before = dal.delay_by_cause.get(DelayCause.TAXI_CONGESTION.value, 0)
    for _ in range(30):
        engine.step()
    after = dal.delay_by_cause.get(DelayCause.TAXI_CONGESTION.value, 0)
    assert after > before


def test_weather_delay_attributed_while_taxiing_slow(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)],
                         weather=WeatherState(taxi_speed_factor=0.7))
    ac = None

    def spawned():
        nonlocal ac
        ac = engine.aircraft.get("UAL1")
        return ac is not None and ac.pending_request == "pushback"
    step_until(engine, spawned, what="spawn")
    engine.apply_instruction(ApprovePushback(callsign="UAL1"))
    step_until(engine, lambda: ac.state == AircraftState.TAXI_OUT,
               what="ready")
    engine.apply_instruction(
        TaxiInstruction(callsign="UAL1", route=["A4", "A5"]))
    for _ in range(60):
        engine.step()
    weather = ac.delay_by_cause.get(DelayCause.WEATHER.value, 0)
    # Moving at 70% speed: ~0.3 s of weather delay per moving second.
    assert weather > 10


def test_unjustified_hold_is_controller_error(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ac = None

    def spawned():
        nonlocal ac
        ac = engine.aircraft.get("UAL1")
        return ac is not None and ac.pending_request == "pushback"
    step_until(engine, spawned, what="spawn")
    engine.apply_instruction(ApprovePushback(callsign="UAL1"))
    step_until(engine, lambda: ac.state == AircraftState.TAXI_OUT,
               what="ready")
    engine.apply_instruction(
        TaxiInstruction(callsign="UAL1", route=["A4", "A5"]))
    for _ in range(30):
        engine.step()
    engine.apply_instruction(HoldPosition(callsign="UAL1"))
    for _ in range(40):
        engine.step()
    # Nobody within 250 m: the hold serves no purpose.
    err = ac.delay_by_cause.get(DelayCause.ATC_INSTRUCTION_ERROR.value, 0)
    assert err >= 35


def test_arrival_queue_delay_past_schedule(airport):
    """An arrival left circling past its scheduled touchdown accrues
    RUNWAY_QUEUE delay (go-around laps included)."""
    engine = make_engine(airport, [arr("AAL9", "G1", sched=300)])
    step_until(engine, lambda: "AAL9" in engine.aircraft, what="spawn")
    ac = engine.aircraft["AAL9"]
    step_until(engine, lambda: ac.go_arounds > 0, what="go around",
               max_ticks=400)
    for _ in range(120):
        engine.step()
    assert ac.delay_by_cause.get(DelayCause.RUNWAY_QUEUE.value, 0) > 100


def test_min_taxi_benchmark_set_and_report_shape(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ac = None

    def spawned():
        nonlocal ac
        ac = engine.aircraft.get("UAL1")
        return ac is not None and ac.pending_request == "pushback"
    step_until(engine, spawned, what="spawn")
    engine.apply_instruction(ApprovePushback(callsign="UAL1"))
    step_until(engine, lambda: ac.state == AircraftState.TAXI_OUT,
               what="ready")
    engine.apply_instruction(
        TaxiInstruction(callsign="UAL1", route=ROUTE_P2_TO_HS05))
    engine.apply_instruction(CrossRunway(callsign="UAL1", runway="14/32"))
    step_until(engine, lambda: ac.state == AircraftState.HOLD_SHORT,
               what="at runway", max_ticks=3000)
    assert ac.min_taxi_time_s is not None
    assert ac.actual_taxi_time_s >= ac.min_taxi_time_s * 0.95

    report = delay_report(engine)
    entry = report["aircraft"][0]
    assert entry["callsign"] == "UAL1"
    assert entry["taxi_ratio"] >= 0.95
    assert "delay_by_cause" in entry
    assert isinstance(entry["transitions"], list)

    metrics = compute_metrics(engine)
    for key in ("total_delay_min", "on_time_performance",
                "throughput_ops_per_hour", "efficiency_score",
                "incidents", "delay_by_cause_min"):
        assert key in metrics
