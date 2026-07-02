from atc.instructions import (
    ApprovePushback,
    ContactNextFrequency,
    CrossRunway,
    RunwayClearance,
    TaxiInstruction,
)
from atc.models import AircraftState, ClearanceType

from conftest import ROUTE_P2_TO_HS05, arr, dep, make_engine, step_until


def stage_departure(engine, cs="UAL1", route=ROUTE_P2_TO_HS05):
    """Drive a departure from gate to HOLD_SHORT at HS_05."""
    ac = None

    def spawned():
        nonlocal ac
        ac = engine.aircraft.get(cs)
        return ac is not None and \
            ac.pending_request == "pushback"
    step_until(engine, spawned, what="pushback request")
    assert engine.apply_instruction(ApprovePushback(callsign=cs)).ok
    step_until(engine, lambda: ac.state == AircraftState.TAXI_OUT,
               what="pushback complete")
    assert engine.apply_instruction(
        TaxiInstruction(callsign=cs, route=route)).ok
    step_until(engine,
               lambda: ac.position.node == "HS_A_E" and ac.speed_ms == 0,
               what="hold short of 14/32")
    assert engine.apply_instruction(
        CrossRunway(callsign=cs, runway="14/32")).ok
    step_until(engine, lambda: ac.state == AircraftState.HOLD_SHORT,
               what="hold short of 05")
    assert engine.apply_instruction(ContactNextFrequency(callsign=cs)).ok
    return ac


def test_departure_full_lifecycle(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ac = stage_departure(engine)
    assert ac.actual_offblock_s is not None
    assert ac.min_taxi_time_s is not None and ac.min_taxi_time_s > 0
    assert ac.actual_taxi_time_s > 0

    r = engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="05",
        clearance_type=ClearanceType.LINE_UP_AND_WAIT))
    assert r.ok, r.reason
    step_until(engine, lambda: ac.state == AircraftState.LINE_UP_WAIT,
               what="line up and wait")
    assert engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="05",
        clearance_type=ClearanceType.TAKEOFF)).ok
    step_until(engine, lambda: ac.state == AircraftState.TAKEOFF_ROLL,
               what="takeoff roll")
    step_until(engine, lambda: ac.state == AircraftState.DEPARTED,
               what="departed")
    assert ac.actual_takeoff_s is not None
    assert engine.completed_takeoffs == 1

    seen = [tr.state for tr in ac.transitions]
    assert seen == [
        AircraftState.PUSHBACK, AircraftState.TAXI_OUT,
        AircraftState.HOLD_SHORT, AircraftState.LINE_UP_WAIT,
        AircraftState.TAKEOFF_ROLL, AircraftState.DEPARTED,
    ]


def test_advance_takeoff_clearance_consumed_at_hold_short(airport):
    """Clearance issued while still taxiing is used on arrival."""
    engine = make_engine(airport, [dep("UAL2", "G3", sched=10)])
    ac = None

    def spawned():
        nonlocal ac
        ac = engine.aircraft.get("UAL2")
        return ac is not None and ac.pending_request == "pushback"
    step_until(engine, spawned, what="spawn")
    engine.apply_instruction(ApprovePushback(callsign="UAL2"))
    step_until(engine, lambda: ac.state == AircraftState.TAXI_OUT,
               what="taxi out")
    engine.apply_instruction(
        TaxiInstruction(callsign="UAL2", route=ROUTE_P2_TO_HS05))
    engine.apply_instruction(CrossRunway(callsign="UAL2", runway="14/32"))
    engine.apply_instruction(ContactNextFrequency(callsign="UAL2"))
    # Clear for takeoff while the aircraft is still taxiing.
    step_until(engine, lambda: ac.position.node == "A3" or (
        ac.position.edge_b in ("A2", "A1", "HS_05")), what="mid taxi")
    r = engine.apply_instruction(RunwayClearance(
        callsign="UAL2", runway="05", clearance_type=ClearanceType.TAKEOFF))
    assert r.ok, r.reason
    step_until(engine, lambda: ac.state == AircraftState.DEPARTED,
               what="departed", max_ticks=3000)


def test_arrival_full_lifecycle(airport):
    engine = make_engine(airport, [arr("AAL9", "G1", sched=300)])
    ac = None

    def spawned():
        nonlocal ac
        ac = engine.aircraft.get("AAL9")
        return ac is not None
    step_until(engine, spawned, what="spawn")
    assert ac.state == AircraftState.ARRIVING
    assert ac.pending_request == "landing"
    r = engine.apply_instruction(RunwayClearance(
        callsign="AAL9", runway="05", clearance_type=ClearanceType.LAND))
    assert r.ok, r.reason
    step_until(engine, lambda: ac.state == AircraftState.LANDING_ROLL,
               what="touchdown", max_ticks=400)
    assert ac.actual_touchdown_s is not None
    step_until(engine, lambda: ac.state == AircraftState.TAXI_IN,
               what="vacated", max_ticks=200)
    # E145 stops within ~750 m; it must take the first exit (E1 -> A2).
    step_until(engine, lambda: ac.position.node == "A2", max_ticks=120,
               what="clear of runway at A2")
    assert ac.pending_request == "taxi_in"
    engine.apply_instruction(ContactNextFrequency(callsign="AAL9"))
    engine.apply_instruction(TaxiInstruction(
        callsign="AAL9",
        route=["A3", "HS_A_W", "RW14_A", "HS_A_E", "A4", "P2", "P1", "G1"]))
    engine.apply_instruction(CrossRunway(callsign="AAL9", runway="14/32"))
    step_until(engine, lambda: ac.state == AircraftState.AT_GATE,
               what="at gate", max_ticks=1000)
    assert ac.actual_ongate_s is not None
    assert engine.completed_landings == 1


def test_pushback_duration_honored(airport):
    engine = make_engine(airport, [dep("SWA3", "G3", sched=10)])
    ac = None

    def spawned():
        nonlocal ac
        ac = engine.aircraft.get("SWA3")
        return ac is not None and ac.pending_request == "pushback"
    step_until(engine, spawned, what="spawn")
    engine.apply_instruction(ApprovePushback(callsign="SWA3"))
    start = engine.t
    step_until(engine, lambda: ac.state == AircraftState.TAXI_OUT,
               what="taxi out")
    assert engine.t - start >= ac.profile.pushback_duration_s
    assert ac.position.node == "P2"
