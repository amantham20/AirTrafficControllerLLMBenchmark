"""Validation-layer rejection tests: malformed or procedurally wrong
instructions must be rejected with the right code and severity."""

from atc.instructions import (
    ApprovePushback,
    ContactNextFrequency,
    CrossRunway,
    HoldPosition,
    RejectCode,
    ResumeTaxi,
    RunwayClearance,
    TaxiInstruction,
)
from atc.models import AircraftState, ClearanceType, Severity

from conftest import ROUTE_P2_TO_HS05, arr, dep, make_engine, step_until
from test_state_machine import stage_departure


def ready_taxi_aircraft(engine, cs="UAL1"):
    ac = None

    def spawned():
        nonlocal ac
        ac = engine.aircraft.get(cs)
        return ac is not None and ac.pending_request == "pushback"
    step_until(engine, spawned, what="spawn")
    engine.apply_instruction(ApprovePushback(callsign=cs))
    step_until(engine, lambda: ac.state == AircraftState.TAXI_OUT,
               what="taxi ready")
    return ac


def test_unknown_callsign(airport):
    engine = make_engine(airport, [])
    r = engine.apply_instruction(HoldPosition(callsign="GHOST1"))
    assert not r.ok and r.code == RejectCode.UNKNOWN_CALLSIGN


def test_route_must_connect(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ready_taxi_aircraft(engine)
    r = engine.apply_instruction(TaxiInstruction(
        callsign="UAL1", route=["A4", "A1"]))  # A4-A1 is not an edge
    assert not r.ok and r.code == RejectCode.ROUTE_DISCONNECTED


def test_route_must_start_at_position(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ready_taxi_aircraft(engine)  # at P2
    r = engine.apply_instruction(TaxiInstruction(
        callsign="UAL1", route=["A2", "A1"]))
    assert not r.ok and r.code == RejectCode.ROUTE_NOT_FROM_POSITION


def test_route_may_not_taxi_along_runway(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ready_taxi_aircraft(engine)
    r = engine.apply_instruction(TaxiInstruction(
        callsign="UAL1",
        route=["A4", "HS_D_N", "RW05_D", "RWX"]))  # RW05_D-RWX is runway
    assert not r.ok and r.code == RejectCode.ROUTE_ALONG_RUNWAY


def test_hold_short_point_must_be_on_route(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ready_taxi_aircraft(engine)
    r = engine.apply_instruction(TaxiInstruction(
        callsign="UAL1", route=["A4", "A5"], hold_short_at="HS_05"))
    assert not r.ok and r.code == RejectCode.HOLD_POINT_NOT_ON_ROUTE


def test_runway_clearance_requires_tower_frequency(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ac = ready_taxi_aircraft(engine)
    engine.apply_instruction(
        TaxiInstruction(callsign="UAL1", route=ROUTE_P2_TO_HS05))
    engine.apply_instruction(CrossRunway(callsign="UAL1", runway="14/32"))
    step_until(engine, lambda: ac.state == AircraftState.HOLD_SHORT,
               what="holding short", max_ticks=3000)
    # Still on ground frequency.
    r = engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="05", clearance_type=ClearanceType.TAKEOFF))
    assert not r.ok and r.code == RejectCode.WRONG_FREQUENCY
    assert r.severity == Severity.MINOR


def test_luaw_requires_being_at_hold_point(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    engine.scenario.active_ends.append("23")
    ac = ready_taxi_aircraft(engine)
    engine.apply_instruction(ContactNextFrequency(callsign="UAL1"))
    # No route toward runway 23's hold point at all.
    r = engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="23",
        clearance_type=ClearanceType.LINE_UP_AND_WAIT))
    assert not r.ok and r.code == RejectCode.NOT_AT_RUNWAY_HOLD_POINT


def test_landing_clearance_must_match_approach(airport):
    engine = make_engine(airport, [arr("AAL9", "G1", sched=300)])
    step_until(engine, lambda: "AAL9" in engine.aircraft, what="spawn")
    r = engine.apply_instruction(RunwayClearance(
        callsign="AAL9", runway="23", clearance_type=ClearanceType.LAND))
    assert not r.ok and r.code == RejectCode.WRONG_APPROACH


def test_inactive_runway_rejected_active_config(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)],
                         active_ends=["23"])
    ac = stage_departure(engine, "UAL1")  # plan says 05, config says 23
    r = engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="05", clearance_type=ClearanceType.TAKEOFF))
    assert not r.ok and r.code == RejectCode.INACTIVE_RUNWAY


def test_closed_runway_rejected(airport):
    engine = make_engine(airport, [arr("AAL9", "G1", sched=300)])
    engine.closed_runways.add("05/23")
    step_until(engine, lambda: "AAL9" in engine.aircraft, what="spawn")
    r = engine.apply_instruction(RunwayClearance(
        callsign="AAL9", runway="05", clearance_type=ClearanceType.LAND))
    assert not r.ok and r.code == RejectCode.RUNWAY_CLOSED
    assert r.severity == Severity.MAJOR


def test_emergency_aircraft_exempt_from_inactive_runway(airport):
    engine = make_engine(airport, [arr("AAL9", "G1", sched=300)],
                         active_ends=["23"])
    step_until(engine, lambda: "AAL9" in engine.aircraft, what="spawn")
    ac = engine.aircraft["AAL9"]
    r1 = engine.apply_instruction(RunwayClearance(
        callsign="AAL9", runway="05", clearance_type=ClearanceType.LAND))
    assert not r1.ok and r1.code == RejectCode.INACTIVE_RUNWAY
    ac.emergency = True
    r2 = engine.apply_instruction(RunwayClearance(
        callsign="AAL9", runway="05", clearance_type=ClearanceType.LAND))
    assert r2.ok


def test_cross_runway_requires_crossing_on_route(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ready_taxi_aircraft(engine)
    r = engine.apply_instruction(CrossRunway(callsign="UAL1",
                                             runway="05/23"))
    assert not r.ok and r.code == RejectCode.NO_CROSSING_AHEAD


def test_resume_requires_active_hold(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ready_taxi_aircraft(engine)
    r = engine.apply_instruction(ResumeTaxi(callsign="UAL1"))
    assert not r.ok and r.code == RejectCode.NOTHING_TO_RESUME


def test_hold_then_resume(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ac = ready_taxi_aircraft(engine)
    engine.apply_instruction(
        TaxiInstruction(callsign="UAL1", route=["A4", "A5"]))
    for _ in range(10):
        engine.step()
    assert ac.speed_ms > 0
    assert engine.apply_instruction(HoldPosition(callsign="UAL1")).ok
    engine.step()
    assert ac.speed_ms == 0
    pos_frozen = (ac.position.edge_a, ac.position.edge_b, ac.position.dist_m)
    for _ in range(10):
        engine.step()
    assert (ac.position.edge_a, ac.position.edge_b,
            ac.position.dist_m) == pos_frozen
    assert engine.apply_instruction(ResumeTaxi(callsign="UAL1")).ok
    for _ in range(5):
        engine.step()
    assert ac.speed_ms > 0


def test_contact_next_frequency_rules(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ac = ready_taxi_aircraft(engine)
    assert ac.frequency.value == "GROUND"
    assert engine.apply_instruction(
        ContactNextFrequency(callsign="UAL1")).ok
    assert ac.frequency.value == "TOWER"
    # Departure already on tower: no next frequency (until airborne).
    r = engine.apply_instruction(ContactNextFrequency(callsign="UAL1"))
    assert not r.ok and r.code == RejectCode.NO_NEXT_FREQUENCY


def test_rejections_are_logged_as_events(airport):
    engine = make_engine(airport, [])
    engine.apply_instruction(HoldPosition(callsign="GHOST1"))
    rej = [e for e in engine.events
           if e.type.value == "instruction_rejected"]
    assert len(rej) == 1
    assert rej[0].data["code"] == "UNKNOWN_CALLSIGN"
    assert engine.rejection_count == 1
