"""Conflict detection tests — the part that must never be wrong."""

from atc.instructions import (
    ApprovePushback,
    ContactNextFrequency,
    CrossRunway,
    RejectCode,
    RunwayClearance,
    TaxiInstruction,
)
from atc.models import (
    AircraftState,
    ClearanceType,
    IncidentType,
    Position,
    Severity,
)

from conftest import (
    ROUTE_P2_TO_HS05,
    ROUTE_P3_TO_HS05,
    arr,
    dep,
    make_engine,
    step_until,
)
from test_state_machine import stage_departure


def incidents_of(engine, itype):
    return [e for e in engine.events if e.incident == itype]


def test_hold_short_compliance_without_clearance(airport):
    """An aircraft must stop at a hold-short line until cleared across."""
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ac = None

    def spawned():
        nonlocal ac
        ac = engine.aircraft.get("UAL1")
        return ac is not None and ac.pending_request == "pushback"
    step_until(engine, spawned, what="spawn")
    engine.apply_instruction(ApprovePushback(callsign="UAL1"))
    step_until(engine, lambda: ac.state == AircraftState.TAXI_OUT,
               what="taxi ready")
    engine.apply_instruction(
        TaxiInstruction(callsign="UAL1", route=ROUTE_P2_TO_HS05))
    step_until(engine,
               lambda: ac.position.node == "HS_A_E" and ac.speed_ms == 0,
               what="stopped at hold short")
    # It must stay there: no crossing clearance was given.
    for _ in range(60):
        engine.step()
    assert ac.position.node == "HS_A_E"
    assert not incidents_of(engine, IncidentType.RUNWAY_INCURSION)
    # Once cleared, it crosses and the clearance is consumed on the far side.
    engine.apply_instruction(CrossRunway(callsign="UAL1", runway="14/32"))
    step_until(engine, lambda: ac.position.node == "A3" or
               (ac.position.edge_a == "HS_A_W"), what="across the runway")
    assert "14/32" not in engine.issued["UAL1"].cross_runways
    assert not incidents_of(engine, IncidentType.RUNWAY_INCURSION)


def test_takeoff_clearance_rejected_when_runway_occupied(airport):
    """Only one aircraft may use a runway: LUAW occupant blocks takeoff."""
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10),
                                   dep("DAL2", "G5", sched=40)])
    ual = stage_departure(engine, "UAL1")
    engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="05",
        clearance_type=ClearanceType.LINE_UP_AND_WAIT))
    step_until(engine, lambda: ual.state == AircraftState.LINE_UP_WAIT,
               what="UAL1 lined up")

    dal = engine.aircraft["DAL2"]
    engine.apply_instruction(ApprovePushback(callsign="DAL2"))
    step_until(engine, lambda: dal.state == AircraftState.TAXI_OUT,
               what="DAL2 taxi ready")
    engine.apply_instruction(
        TaxiInstruction(callsign="DAL2", route=ROUTE_P3_TO_HS05))
    engine.apply_instruction(ContactNextFrequency(callsign="DAL2"))
    r = engine.apply_instruction(RunwayClearance(
        callsign="DAL2", runway="05", clearance_type=ClearanceType.TAKEOFF))
    assert not r.ok
    assert r.code == RejectCode.RUNWAY_OCCUPIED
    assert r.severity == Severity.CRITICAL


def test_wake_separation_enforced(airport):
    """A takeoff clearance too soon after the previous departure is
    rejected with the wake interval in the reason."""
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10,
                                       type_code="B763"),
                                   dep("DAL2", "G5", sched=40)])
    ual = stage_departure(engine, "UAL1")

    dal = engine.aircraft["DAL2"]
    engine.apply_instruction(ApprovePushback(callsign="DAL2"))
    step_until(engine, lambda: dal.state == AircraftState.TAXI_OUT,
               what="DAL2 ready")
    engine.apply_instruction(
        TaxiInstruction(callsign="DAL2", route=ROUTE_P3_TO_HS05))
    engine.apply_instruction(CrossRunway(callsign="DAL2", runway="14/32"))
    engine.apply_instruction(ContactNextFrequency(callsign="DAL2"))
    # DAL2 queues just behind the occupied hold point.
    step_until(engine, lambda: dal.clearances.taxi_route == ["HS_05"] and
               dal.speed_ms == 0, what="DAL2 queued", max_ticks=3000)

    engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="05", clearance_type=ClearanceType.TAKEOFF))
    step_until(engine, lambda: ual.state == AircraftState.DEPARTED,
               what="heavy departed")
    step_until(engine, lambda: dal.state == AircraftState.HOLD_SHORT,
               what="DAL2 holding short", max_ticks=120)
    assert engine.t - ual.actual_takeoff_s < 100

    # Immediately behind a heavy: medium needs 120 s.
    r = engine.apply_instruction(RunwayClearance(
        callsign="DAL2", runway="05", clearance_type=ClearanceType.TAKEOFF))
    assert not r.ok
    assert r.code == RejectCode.WAKE_SEPARATION
    assert r.severity == Severity.MAJOR
    # After the interval has elapsed it is accepted.
    while engine.t - ual.actual_takeoff_s < 121:
        engine.step()
    r2 = engine.apply_instruction(RunwayClearance(
        callsign="DAL2", runway="05", clearance_type=ClearanceType.TAKEOFF))
    assert r2.ok, r2.reason


def test_takeoff_rejected_with_arrival_on_short_final(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10),
                                   arr("AAL9", "G1", sched=700)])
    ual = stage_departure(engine, "UAL1")
    step_until(engine, lambda: "AAL9" in engine.aircraft, what="arrival spawn")
    aal = engine.aircraft["AAL9"]
    assert aal.state == AircraftState.ARRIVING

    def eta():
        return (aal.position.air_dist_m or 0) / aal.profile.approach_speed_ms

    step_until(engine, lambda: eta() < 95, what="arrival inside 95s")
    r = engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="05", clearance_type=ClearanceType.TAKEOFF))
    assert not r.ok and r.code == RejectCode.ARRIVAL_ON_SHORT_FINAL
    assert r.severity == Severity.MAJOR
    step_until(engine, lambda: eta() < 40, what="arrival inside 40s")
    r2 = engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="05", clearance_type=ClearanceType.TAKEOFF))
    assert not r2.ok and r2.severity == Severity.CRITICAL


def test_crossing_clearance_rejected_during_active_operation(airport):
    """Clearing a crossing while a takeoff roll is under way on that runway
    is a critical controller error."""
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10),
                                   dep("N123", "G1", sched=30,
                                       type_code="C172")])
    ual = stage_departure(engine, "UAL1")

    n123 = engine.aircraft["N123"]
    engine.apply_instruction(ApprovePushback(callsign="N123"))
    step_until(engine, lambda: n123.state == AircraftState.TAXI_OUT,
               what="N123 ready")
    engine.apply_instruction(TaxiInstruction(
        callsign="N123", route=["P2", "A4", "HS_A_E"]))
    step_until(engine, lambda: n123.position.node == "HS_A_E" and
               n123.speed_ms == 0, what="N123 at crossing")

    engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="05", clearance_type=ClearanceType.TAKEOFF))
    step_until(engine, lambda: ual.state == AircraftState.TAKEOFF_ROLL,
               what="UAL1 rolling")
    # 14/32 shares the intersection with 05/23: crossing is unsafe.
    r = engine.apply_instruction(
        CrossRunway(callsign="N123", runway="14/32"))
    assert not r.ok
    assert r.code == RejectCode.CROSSING_UNSAFE
    assert r.severity == Severity.CRITICAL


def test_pilot_refuses_crossing_with_traffic_rolling(airport):
    """Spec: pilots report 'unable' if cleared to cross with an aircraft on
    the runway. Cross clearance granted BEFORE the roll starts, aircraft
    reaches the hold line while traffic is rolling."""
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10),
                                   dep("N123", "G1", sched=30,
                                       type_code="C172")])
    ual = stage_departure(engine, "UAL1")

    n123 = engine.aircraft["N123"]
    engine.apply_instruction(ApprovePushback(callsign="N123"))
    step_until(engine, lambda: n123.state == AircraftState.TAXI_OUT,
               what="N123 ready")
    engine.apply_instruction(TaxiInstruction(
        callsign="N123", route=["P2", "A4", "HS_A_E", "RW14_A", "HS_A_W"]))
    engine.apply_instruction(CrossRunway(callsign="N123", runway="14/32"))
    step_until(engine, lambda: n123.position.edge_b == "HS_A_E" and
               n123.position.dist_m > 380, what="N123 short of the line",
               max_ticks=3000)

    # Manufacture the race: UAL1 rolling mid-runway on the pavement shared
    # with 14/32 (edge RW05_E1->RWX) exactly as N123 reaches the hold line.
    engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="05", clearance_type=ClearanceType.TAKEOFF))
    step_until(engine, lambda: ual.state == AircraftState.TAKEOFF_ROLL,
               what="UAL1 rolling")
    engine._roll_dist["UAL1"] = 900.0
    engine._roll_speed["UAL1"] = 0.0
    step_until(engine, lambda: n123.position.node == "HS_A_E" and
               n123.speed_ms == 0, what="N123 held at the line",
               max_ticks=25)
    unable = [e for e in engine.events if e.type.value == "pilot_unable"]
    assert unable, "pilot should have refused the crossing"
    assert "14/32" not in engine.issued["N123"].cross_runways
    assert n123.position.node == "HS_A_E"


def test_runway_incursion_detected_on_unauthorized_entry(airport):
    """A readback error (heard 'cross' where none was issued) must fire a
    critical runway incursion when the aircraft enters the runway."""
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ac = None

    def spawned():
        nonlocal ac
        ac = engine.aircraft.get("UAL1")
        return ac is not None and ac.pending_request == "pushback"
    step_until(engine, spawned, what="spawn")
    engine.apply_instruction(ApprovePushback(callsign="UAL1"))
    step_until(engine, lambda: ac.state == AircraftState.TAXI_OUT,
               what="taxi ready")
    engine.apply_instruction(
        TaxiInstruction(callsign="UAL1", route=ROUTE_P2_TO_HS05))
    # Simulate the mishearing: the PILOT believes they may cross, ATC never
    # issued it (aircraft clearances mutate, issued truth does not).
    ac.clearances.cross_runways.add("14/32")
    step_until(engine, lambda: bool(
        incidents_of(engine, IncidentType.RUNWAY_INCURSION)),
        what="incursion detected", max_ticks=600)
    inc = incidents_of(engine, IncidentType.RUNWAY_INCURSION)[0]
    assert inc.severity == Severity.CRITICAL
    assert inc.callsign == "UAL1"


def test_landing_without_clearance_goes_around(airport):
    engine = make_engine(airport, [arr("AAL9", "G1", sched=300)])
    ac = None

    def spawned():
        nonlocal ac
        ac = engine.aircraft.get("AAL9")
        return ac is not None
    step_until(engine, spawned, what="spawn")
    step_until(engine, lambda: ac.go_arounds > 0, what="go around",
               max_ticks=400)
    assert ac.state == AircraftState.ARRIVING
    assert (ac.position.air_dist_m or 0) > 1000
    gos = incidents_of(engine, IncidentType.GO_AROUND)
    assert gos and gos[0].severity == Severity.MINOR
    assert engine.completed_landings == 0


def test_landing_on_occupied_runway_is_critical(airport):
    """Cleared to land while another aircraft sits on the runway: near-miss
    incident, forced go-around, controller at fault."""
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10),
                                   arr("AAL9", "G1", sched=800)])
    ual = stage_departure(engine, "UAL1")
    engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="05",
        clearance_type=ClearanceType.LINE_UP_AND_WAIT))
    step_until(engine, lambda: ual.state == AircraftState.LINE_UP_WAIT,
               what="UAL1 lined up")

    step_until(engine, lambda: "AAL9" in engine.aircraft,
               what="arrival spawn")
    aal = engine.aircraft["AAL9"]
    r = engine.apply_instruction(RunwayClearance(
        callsign="AAL9", runway="05", clearance_type=ClearanceType.LAND))
    assert r.ok  # anticipated separation is legal at clearance time
    step_until(engine, lambda: aal.go_arounds > 0, what="go around",
               max_ticks=900)
    nms = incidents_of(engine, IncidentType.NEAR_MISS)
    assert nms and any(e.severity == Severity.CRITICAL for e in nms)
    assert aal.atc_fault_go_around
    assert engine.completed_landings == 0


def test_collision_detection(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10),
                                   dep("DAL2", "G5", sched=10)])
    step_until(engine, lambda: len(engine.aircraft) == 2, what="spawn")
    a = engine.aircraft["UAL1"]
    b = engine.aircraft["DAL2"]
    # Force both mid-taxiway nose to nose.
    a.state = AircraftState.TAXI_OUT
    b.state = AircraftState.TAXI_OUT
    a.position = Position.on_edge("A2", "A3", 100.0)
    b.position = Position.on_edge("A3", "A2", 505.0)  # edge is ~611 m
    engine.step()
    cols = incidents_of(engine, IncidentType.COLLISION)
    assert cols and cols[0].severity == Severity.CRITICAL


def test_deadlock_alert_for_head_on_taxi(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10),
                                   dep("DAL2", "G5", sched=10)])
    step_until(engine, lambda: len(engine.aircraft) == 2, what="spawn")
    a = engine.aircraft["UAL1"]
    b = engine.aircraft["DAL2"]
    a.state = AircraftState.TAXI_OUT
    b.state = AircraftState.TAXI_OUT
    a.position = Position.on_edge("A2", "A3", 100.0)
    a.clearances.taxi_route = ["A3"]
    b.position = Position.on_edge("A3", "A2", 100.0)
    b.clearances.taxi_route = ["A2"]
    for _ in range(5):
        engine.step()
    dls = incidents_of(engine, IncidentType.TAXI_DEADLOCK)
    assert dls, "head-on aircraft on one edge must raise a deadlock alert"


def test_no_false_incursion_during_normal_ops(airport):
    """A takeoff roll through the runway intersection must NOT flag an
    incursion on the crossing runway (transitive authorization)."""
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    ual = stage_departure(engine, "UAL1")
    engine.apply_instruction(RunwayClearance(
        callsign="UAL1", runway="05", clearance_type=ClearanceType.TAKEOFF))
    step_until(engine, lambda: ual.state == AircraftState.DEPARTED,
               what="departed")
    assert not incidents_of(engine, IncidentType.RUNWAY_INCURSION)
    assert not incidents_of(engine, IncidentType.NEAR_MISS)
