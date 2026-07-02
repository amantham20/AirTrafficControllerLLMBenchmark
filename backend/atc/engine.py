"""The simulation engine: tick loop, aircraft state machines, movement,
runway occupancy, conflict detection and delay attribution.

Deterministic: same scenario (seed) + same controller decisions = same run.
The engine implements `SimView` so the instruction validator can query it.
"""

from __future__ import annotations

import random
import time
from typing import Optional, TYPE_CHECKING

from . import phraseology
from .airport import Airport, Runway
from .delays import StopReason, attribute
from .instructions import (
    ApprovePushback,
    ContactNextFrequency,
    CrossRunway,
    HoldPosition,
    Instruction,
    ResumeTaxi,
    RunwayClearance,
    SimView,
    TaxiInstruction,
    ValidationResult,
    Validator,
)
from .models import (
    AIRCRAFT_TYPES,
    Aircraft,
    AircraftState,
    Clearances,
    ClearanceType,
    DelayCause,
    DelayRecord,
    Event,
    EventType,
    FlightKind,
    Frequency,
    IncidentType,
    NodeType,
    Position,
    Severity,
    WakeCategory,
)
from .pilot import CommOutcome, PilotAgent
from .scenario import Scenario, ScenarioEvent, WeatherState

if TYPE_CHECKING:
    from .controllers import Controller

# --- movement constants (meters / seconds) ---------------------------------
FOLLOW_GAP_M = 70.0          # minimum in-trail gap while taxiing
NODE_CLEAR_M = 40.0          # a node is "occupied" if traffic is this close
NODE_STOP_SHORT_M = 60.0     # stop this far short of an occupied node
COLLISION_M = 15.0
NEAR_MISS_M = 40.0
DEPARTURE_SPAWN_LEAD_S = 180
APPROACH_DURATION_S = 240
GO_AROUND_LAP_S = 300
LIFTOFF_CLIMB_FACTOR = 1.2
DEPARTED_REMOVE_DIST_M = 4000.0
REQUEST_REMINDER_S = 90
ARRIVAL_ALERT_S = 75


class SimEngine(SimView):
    def __init__(self, airport: Airport, scenario: Scenario,
                 controller: Optional["Controller"] = None):
        self.airport = airport
        self.scenario = scenario
        self.controller = controller
        self.t = 0
        self.tick_s = 1

        self.rng = random.Random(scenario.seed)
        self.pilot = PilotAgent(
            airport=airport, rng=self.rng,
            rates=scenario.pilot_error_rates.to_rates())

        self.aircraft: dict[str, Aircraft] = {}
        self.order: list[str] = []
        self.events: list[Event] = []
        self._seq = 0
        self.controller_outbox: list[Event] = []
        self._last_scan_t = -10**9

        self.weather: WeatherState = scenario.weather.model_copy()
        self.active_ends: set[str] = set(scenario.active_ends)
        self.closed_runways: set[str] = set()

        # Ground truth clearances (what ATC actually issued); the aircraft's
        # own `clearances` hold what the pilot *heard*.
        self.issued: dict[str, Clearances] = {}

        self.last_runway_op: dict[str, tuple[int, WakeCategory]] = {}
        self._pushback_done_at: dict[str, int] = {}
        self._roll_dist: dict[str, float] = {}
        self._roll_speed: dict[str, float] = {}
        self._exit_plan: dict[str, tuple[float, str, str]] = {}
        self._lineup_end: dict[str, str] = {}
        self._delayed_comms: list[tuple[int, str, Instruction, str]] = []
        self._force_go_around: set[str] = set()
        self._unable_crossing_flagged: set[str] = set()
        # Runways an aircraft has physically entered under a cross clearance;
        # the clearance is consumed only once the crossing completes.
        self._crossing_started: dict[str, set[str]] = {}

        self._incident_cooldown: dict[str, int] = {}
        self._stopped_reason: dict[str, StopReason] = {}
        self._pending_events = sorted(scenario.events, key=lambda e: e.at_s)
        self._request_last_emit: dict[str, int] = {}

        # Stats
        self.completed_takeoffs = 0
        self.completed_landings = 0
        self.decision_log: list[dict] = []   # {t, latency_ms, n_instructions}
        self.rejection_count = 0

    # ------------------------------------------------------------------
    # Event log
    # ------------------------------------------------------------------

    def emit(self, type: EventType, text: str, callsign: Optional[str] = None,
             data: Optional[dict] = None, severity: Optional[Severity] = None,
             incident: Optional[IncidentType] = None,
             to_controller: bool = True) -> Event:
        self._seq += 1
        ev = Event(seq=self._seq, t_s=self.t, type=type, callsign=callsign,
                   text=text, data=data or {}, severity=severity,
                   incident=incident)
        self.events.append(ev)
        if to_controller:
            self.controller_outbox.append(ev)
        return ev

    def emit_controller_note(self, text: str) -> None:
        """Controller commentary / adapter diagnostics: shown in the
        transcript, never fed back to the controller."""
        self.emit(EventType.SYSTEM, f"[controller] {text}",
                  to_controller=False)

    # ------------------------------------------------------------------
    # SimView interface (used by the Validator)
    # ------------------------------------------------------------------

    def get_aircraft(self, callsign: str) -> Optional[Aircraft]:
        return self.aircraft.get(callsign)

    def _ground_active(self, ac: Aircraft) -> bool:
        return ac.state in (
            AircraftState.PUSHBACK, AircraftState.TAXI_OUT,
            AircraftState.HOLD_SHORT, AircraftState.LINE_UP_WAIT,
            AircraftState.TAKEOFF_ROLL, AircraftState.LANDING_ROLL,
            AircraftState.TAXI_IN)

    def runway_physically_occupied_by(self, runway_id: str,
                                      exclude: str) -> list[str]:
        out = []
        for cs in self.order:
            ac = self.aircraft[cs]
            if cs == exclude or not self._ground_active(ac):
                continue
            if runway_id in self.airport.runways_touching_position(
                    ac.position):
                out.append(cs)
        return out

    def _operation_runway(self, ac: Aircraft) -> Optional[Runway]:
        """The runway an aircraft has an active operation on, if any."""
        issued = self.issued.get(ac.callsign, ac.clearances)
        if ac.state in (AircraftState.TAKEOFF_ROLL,
                        AircraftState.LINE_UP_WAIT):
            end = issued.runway_clearance_end or ac.plan.runway
            return self.airport.runway_for_end(end)
        if ac.state == AircraftState.LANDING_ROLL:
            return self.airport.runway_for_end(ac.plan.runway)
        if ac.state in (AircraftState.TAXI_OUT, AircraftState.HOLD_SHORT) \
                and issued.runway_clearance in (
                    ClearanceType.TAKEOFF, ClearanceType.LINE_UP_AND_WAIT):
            return self.airport.runway_for_end(issued.runway_clearance_end)
        return None

    def runway_operation_blockers(self, runway_id: str,
                                  exclude: str) -> list[str]:
        target = self.airport.runways[runway_id]
        out = []
        for cs in self.order:
            if cs == exclude:
                continue
            ac = self.aircraft[cs]
            op_rw = self._operation_runway(ac)
            if op_rw is not None and (op_rw.id == runway_id or
                                      op_rw.node_set & target.node_set):
                out.append(cs)
        return out

    def arrival_seconds_to_runway(self, runway_id: str,
                                  exclude: str) -> Optional[float]:
        target = self.airport.runways[runway_id]
        best: Optional[float] = None
        for cs in self.order:
            if cs == exclude:
                continue
            ac = self.aircraft[cs]
            if ac.state != AircraftState.ARRIVING:
                continue
            rw = self.airport.runway_for_end(ac.plan.runway)
            if rw is None or not (rw.id == runway_id or
                                  rw.node_set & target.node_set):
                continue
            eta = (ac.position.air_dist_m or 0.0) / ac.profile.approach_speed_ms
            if best is None or eta < best:
                best = eta
        return best

    def seconds_since_last_runway_op(
            self, runway_id: str) -> Optional[tuple[float, WakeCategory]]:
        rec = self.last_runway_op.get(runway_id)
        if rec is None:
            return None
        t_op, wake = rec
        return (float(self.t - t_op), wake)

    def is_runway_closed(self, runway_id: str) -> bool:
        return runway_id in self.closed_runways

    def active_runway_ends(self) -> set[str]:
        return set(self.active_ends)

    def separation_multiplier(self) -> float:
        return self.weather.separation_multiplier

    # ------------------------------------------------------------------
    # Spawning & state transitions
    # ------------------------------------------------------------------

    def _spawn_due_aircraft(self) -> None:
        for entry in self.scenario.traffic:
            if entry.callsign in self.aircraft:
                continue
            if entry.kind == FlightKind.DEPARTURE:
                spawn_at = max(0, entry.scheduled_time_s -
                               DEPARTURE_SPAWN_LEAD_S)
            else:
                spawn_at = max(0, entry.scheduled_time_s -
                               APPROACH_DURATION_S)
            if self.t < spawn_at:
                continue
            profile = AIRCRAFT_TYPES[entry.type_code].model_copy()
            plan = entry_to_plan(entry)
            if entry.kind == FlightKind.DEPARTURE:
                ac = Aircraft(
                    callsign=entry.callsign, profile=profile, plan=plan,
                    state=AircraftState.SCHEDULED,
                    position=Position.at_node(entry.gate),
                    frequency=Frequency.GROUND)
            else:
                dist = profile.approach_speed_ms * max(
                    1, entry.scheduled_time_s - self.t)
                ac = Aircraft(
                    callsign=entry.callsign, profile=profile, plan=plan,
                    state=AircraftState.ARRIVING,
                    position=Position(air_dist_m=dist),
                    frequency=Frequency.TOWER,
                    speed_ms=profile.approach_speed_ms)
                ac.pending_request = "landing"
            ac.spawned_at_s = self.t
            ac.state_since_s = self.t
            self.aircraft[entry.callsign] = ac
            self.order.append(entry.callsign)
            self.issued[entry.callsign] = Clearances()

    def _transition(self, ac: Aircraft, new_state: AircraftState,
                    scheduled_s: Optional[int] = None) -> None:
        ac.transitions.append(DelayRecord(
            state=new_state, scheduled_s=scheduled_s, actual_s=self.t))
        ac.state = new_state
        ac.state_since_s = self.t
        self.emit(EventType.STATE_TRANSITION,
                  f"{ac.callsign} -> {new_state.value}",
                  callsign=ac.callsign,
                  data={"state": new_state.value}, to_controller=False)

    # ------------------------------------------------------------------
    # Instruction application
    # ------------------------------------------------------------------

    def apply_instruction(self, instr: Instruction) -> ValidationResult:
        """Validate and apply one controller instruction. This is THE entry
        point for both scripted and LLM controllers."""
        result = Validator(self).validate(instr)
        ac = self.aircraft.get(instr.callsign)
        if not result.ok:
            self.rejection_count += 1
            if ac is not None:
                ac.last_rejection_s = self.t
            self.emit(
                EventType.INSTRUCTION_REJECTED,
                f"[REJECTED {result.severity.value if result.severity else ''}"
                f"] {instr.kind} {instr.callsign}: {result.reason}",
                callsign=instr.callsign,
                data={"instruction": instr.model_dump(),
                      "code": result.code.value if result.code else None,
                      "reason": result.reason},
                severity=result.severity,
                incident=IncidentType.REJECTED_INSTRUCTION)
            return result

        assert ac is not None
        text = phraseology.render_instruction(self.airport, ac, instr)
        self.emit(EventType.ATC_INSTRUCTION, text, callsign=ac.callsign,
                  data={"instruction": instr.model_dump()})

        outcome = self.pilot.communicate(ac, instr)
        if outcome.kind == "stuck_mic":
            self.emit(EventType.PILOT_READBACK,
                      f"({ac.callsign}: no readback received — blocked or "
                      "stuck mic)",
                      callsign=ac.callsign, data={"comm": "stuck_mic"})
            return result
        if outcome.kind == "slow_response":
            readback = phraseology.render_readback(self.airport, ac, instr)
            self._delayed_comms.append(
                (self.t + outcome.delay_s, ac.callsign, instr, readback))
            return result

        applied = outcome.applied_instruction
        assert applied is not None
        readback = phraseology.render_readback(self.airport, ac, applied)
        self.emit(EventType.PILOT_READBACK, readback, callsign=ac.callsign,
                  data={"comm": outcome.kind})
        # Ground truth gets what ATC said; the aircraft acts on what it heard.
        self._apply(ac, instr, heard=False)
        self._apply(ac, applied, heard=True)
        return result

    def _process_delayed_comms(self) -> None:
        due = [d for d in self._delayed_comms if d[0] <= self.t]
        self._delayed_comms = [d for d in self._delayed_comms
                               if d[0] > self.t]
        for _, cs, instr, readback in due:
            ac = self.aircraft.get(cs)
            if ac is None:
                continue
            self.emit(EventType.PILOT_READBACK, readback, callsign=cs,
                      data={"comm": "slow_response"})
            self._apply(ac, instr, heard=False)
            self._apply(ac, instr, heard=True)

    def _clearances_of(self, ac: Aircraft, heard: bool) -> Clearances:
        return ac.clearances if heard else self.issued[ac.callsign]

    def _apply(self, ac: Aircraft, instr: Instruction, heard: bool) -> None:
        cl = self._clearances_of(ac, heard)
        if isinstance(instr, ApprovePushback):
            cl.pushback_approved = True
            if heard and ac.state == AircraftState.SCHEDULED:
                self._transition(ac, AircraftState.PUSHBACK,
                                 scheduled_s=ac.plan.scheduled_time_s)
                ac.actual_offblock_s = self.t
                self._pushback_done_at[ac.callsign] = (
                    self.t + ac.profile.pushback_duration_s)
                ac.pending_request = None
        elif isinstance(instr, TaxiInstruction):
            route = list(instr.route)
            if ac.position.node is not None and route and \
                    route[0] == ac.position.node:
                route = route[1:]
            cl.taxi_route = route
            cl.hold_short_at = instr.hold_short_at
            cl.holding_position = False
            if heard:
                if ac.pending_request in ("taxi", "taxi_in"):
                    ac.pending_request = None
                if ac.state == AircraftState.HOLD_SHORT:
                    self._transition(ac, AircraftState.TAXI_OUT)
                if ac.min_taxi_time_s is None:
                    self._set_min_taxi_benchmark(ac)
                if ac.taxi_started_s is None and ac.state in (
                        AircraftState.TAXI_OUT, AircraftState.TAXI_IN):
                    ac.taxi_started_s = self.t
        elif isinstance(instr, RunwayClearance):
            cl.runway_clearance = instr.clearance_type
            cl.runway_clearance_end = instr.runway
            if heard:
                if instr.clearance_type in (ClearanceType.TAKEOFF,
                                            ClearanceType.LINE_UP_AND_WAIT):
                    if ac.pending_request == "takeoff":
                        ac.pending_request = None
                    self._maybe_enter_runway(ac)
                elif instr.clearance_type == ClearanceType.LAND:
                    if ac.pending_request == "landing":
                        ac.pending_request = None
        elif isinstance(instr, HoldPosition):
            cl.holding_position = True
            if heard:
                ac.speed_ms = 0.0
        elif isinstance(instr, ResumeTaxi):
            cl.holding_position = False
        elif isinstance(instr, CrossRunway):
            rw = self.airport.runways.get(instr.runway) or \
                self.airport.runway_for_end(instr.runway)
            assert rw is not None
            cl.cross_runways.add(rw.id)
            self._unable_crossing_flagged.discard(ac.callsign)
            # A crossing clearance supersedes a hold-short instruction at
            # that runway's hold point.
            if cl.hold_short_at is not None and \
                    self.airport.hold_short_runway(cl.hold_short_at) == rw.id:
                cl.hold_short_at = None
        elif isinstance(instr, ContactNextFrequency):
            if heard:
                if ac.frequency == Frequency.GROUND:
                    ac.frequency = Frequency.TOWER
                elif ac.frequency == Frequency.TOWER:
                    ac.frequency = Frequency.GROUND

    def _set_min_taxi_benchmark(self, ac: Aircraft) -> None:
        """Theoretical minimum taxi time from the current position to the
        flight's taxi destination (runway hold point or gate)."""
        start = ac.position.node
        if start is None:
            start = ac.position.edge_b
        if ac.plan.kind == FlightKind.DEPARTURE:
            rw = self.airport.runway_for_end(ac.plan.runway)
            if rw is None:
                return
            threshold = rw.threshold(ac.plan.runway)
            # Destination: the hold-short node adjacent to the threshold.
            goal = None
            for e in self.airport.adjacency[threshold]:
                other = e.other(threshold)
                if self.airport.hold_short_runway(other) == rw.id:
                    goal = other
                    break
            if goal is None:
                return
        else:
            goal = ac.plan.gate
        path = self.airport.shortest_path(start, goal)
        if path is None or len(path) < 2:
            return
        ac.min_taxi_time_s = self.airport.min_taxi_time_s(
            path, ac.profile.taxi_speed_ms)

    def _maybe_enter_runway(self, ac: Aircraft) -> None:
        """If the aircraft is already holding short of its cleared runway,
        extend its route onto the threshold so movement carries it on."""
        cl = ac.clearances
        if cl.runway_clearance_end is None:
            return
        rw = self.airport.runway_for_end(cl.runway_clearance_end)
        if rw is None:
            return
        threshold = rw.threshold(cl.runway_clearance_end)
        node = ac.position.node
        if node is not None and \
                self.airport.hold_short_runway(node) == rw.id and \
                self.airport.edge_between(node, threshold) is not None and \
                not cl.taxi_route:
            cl.taxi_route = [threshold]

    # ------------------------------------------------------------------
    # Pilot requests
    # ------------------------------------------------------------------

    def _emit_request(self, ac: Aircraft, request: str,
                      force: bool = False) -> None:
        last = self._request_last_emit.get(ac.callsign)
        if not force and last is not None and \
                self.t - last < REQUEST_REMINDER_S:
            return
        self._request_last_emit[ac.callsign] = self.t
        ac.pending_request = request
        self.emit(EventType.PILOT_REQUEST,
                  self.pilot.request_text(ac, request),
                  callsign=ac.callsign, data={"request": request})

    def _generate_pilot_requests(self) -> None:
        for cs in self.order:
            ac = self.aircraft[cs]
            issued = self.issued[cs]
            if ac.state == AircraftState.SCHEDULED and \
                    self.t >= ac.plan.scheduled_time_s and \
                    not ac.clearances.pushback_approved:
                self._emit_request(ac, "pushback")
            elif ac.state == AircraftState.TAXI_OUT and \
                    not ac.clearances.taxi_route and \
                    not ac.clearances.holding_position and \
                    ac.pending_request == "taxi":
                self._emit_request(ac, "taxi")
            elif ac.state == AircraftState.HOLD_SHORT and \
                    issued.runway_clearance is None:
                self._emit_request(ac, "takeoff")
            elif ac.state == AircraftState.ARRIVING and \
                    issued.runway_clearance != ClearanceType.LAND:
                self._emit_request(ac, "landing")
            elif ac.state == AircraftState.TAXI_IN and \
                    not ac.clearances.taxi_route and \
                    ac.pending_request == "taxi_in":
                self._emit_request(ac, "taxi_in")

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    def _node_occupied_by_other(self, node_id: str, me: str) -> bool:
        for cs in self.order:
            if cs == me:
                continue
            other = self.aircraft[cs]
            parked = other.state in (AircraftState.SCHEDULED,
                                     AircraftState.AT_GATE)
            if not self._ground_active(other) and not parked:
                continue
            if parked and other.position.node != node_id:
                continue
            pos = other.position
            if pos.is_airborne:
                continue
            if pos.node == node_id:
                return True
            if pos.edge_a is not None:
                edge = self.airport.edge_between(pos.edge_a, pos.edge_b)
                if edge is None:
                    continue
                if pos.edge_b == node_id and \
                        edge.length_m - pos.dist_m < NODE_CLEAR_M:
                    return True
                if pos.edge_a == node_id and pos.dist_m < NODE_CLEAR_M:
                    return True
        return False

    def _leader_gap_limit(self, ac: Aircraft) -> Optional[float]:
        """Max dist_m we may advance to on the current edge given a leader
        ahead on the same edge, or None if unconstrained."""
        pos = ac.position
        best: Optional[float] = None
        for cs in self.order:
            if cs == ac.callsign:
                continue
            other = self.aircraft[cs]
            opos = other.position
            if opos.edge_a == pos.edge_a and opos.edge_b == pos.edge_b and \
                    opos.dist_m > pos.dist_m:
                limit = opos.dist_m - FOLLOW_GAP_M
                if best is None or limit < best:
                    best = limit
        return best

    def _opposing_on_edge(self, a: str, b: str, me: str) -> Optional[str]:
        for cs in self.order:
            if cs == me:
                continue
            opos = self.aircraft[cs].position
            if opos.edge_a == b and opos.edge_b == a:
                return cs
        return None

    def _stop(self, ac: Aircraft, reason: StopReason) -> None:
        ac.speed_ms = 0.0
        self._stopped_reason[ac.callsign] = reason

    def _hold_short_blocks(self, ac: Aircraft, node_id: str,
                           next_node: str) -> Optional[str]:
        """If `node_id` is a hold-short line and the next hop enters the
        protected runway without clearance, return the runway id."""
        rw_id = self.airport.hold_short_runway(node_id)
        if rw_id is None:
            return None
        rw = self.airport.runways[rw_id]
        if next_node not in rw.node_set:
            return None  # moving away from the runway; no restriction
        cl = ac.clearances
        if rw_id in cl.cross_runways:
            # Pilot sanity check (the "unable" case): never begin a crossing
            # while someone is rolling on that runway.
            for cs in self.runway_physically_occupied_by(rw_id, ac.callsign):
                other = self.aircraft[cs]
                if other.state in (AircraftState.TAKEOFF_ROLL,
                                   AircraftState.LANDING_ROLL):
                    if ac.callsign not in self._unable_crossing_flagged:
                        self._unable_crossing_flagged.add(ac.callsign)
                        cl.cross_runways.discard(rw_id)
                        self.issued[ac.callsign].cross_runways.discard(rw_id)
                        self.emit(
                            EventType.PILOT_UNABLE,
                            self.pilot.unable_text(
                                ac, f"Traffic on runway {rw_id}."),
                            callsign=ac.callsign,
                            data={"runway": rw_id})
                    return rw_id
            return None
        if cl.runway_clearance in (ClearanceType.TAKEOFF,
                                   ClearanceType.LINE_UP_AND_WAIT):
            end = cl.runway_clearance_end
            if end and end in rw.ends and rw.threshold(end) == next_node:
                return None
        return rw_id

    def _advance_taxiing(self, ac: Aircraft) -> None:
        cl = ac.clearances
        if cl.holding_position:
            self._stop(ac, StopReason.ATC_HOLD)
            return
        if not cl.taxi_route:
            if ac.state in (AircraftState.TAXI_OUT, AircraftState.TAXI_IN):
                self._stop(ac, StopReason.NO_INSTRUCTION)
            elif ac.state == AircraftState.HOLD_SHORT:
                self._stop(ac, StopReason.HOLD_SHORT_WAIT)
            return

        time_left = float(self.tick_s)
        moved = False
        while time_left > 1e-9 and cl.taxi_route:
            pos = ac.position
            if pos.node is not None:
                nxt = cl.taxi_route[0]
                # Instructed hold-short point.
                if cl.hold_short_at is not None and \
                        pos.node == cl.hold_short_at:
                    self._stop(ac, StopReason.ATC_HOLD_SHORT)
                    self._maybe_hold_short_state(ac, pos.node)
                    return
                # Physical hold-short line.
                blocked_rw = self._hold_short_blocks(ac, pos.node, nxt)
                if blocked_rw is not None:
                    self._stop(ac, StopReason.HOLD_SHORT_WAIT)
                    self._maybe_hold_short_state(ac, pos.node)
                    return
                edge = self.airport.edge_between(pos.node, nxt)
                if edge is None:
                    # Should be impossible (validated); fail safe.
                    self._stop(ac, StopReason.NO_INSTRUCTION)
                    cl.taxi_route = []
                    return
                if self._opposing_on_edge(pos.node, nxt, ac.callsign):
                    self._stop(ac, StopReason.BLOCKED_TRAFFIC)
                    return
                ac.position = Position.on_edge(pos.node, nxt, 0.0)
                ac.heading_deg = self.airport.heading_between(pos.node, nxt)
                pos = ac.position
                touched = self.airport.runways_touching_position(pos)
                for rw_id in touched & cl.cross_runways:
                    self._crossing_started.setdefault(
                        ac.callsign, set()).add(rw_id)

            edge = self.airport.edge_between(pos.edge_a, pos.edge_b)
            speed = min(ac.profile.taxi_speed_ms, edge.speed_limit_ms) * \
                self.weather.taxi_speed_factor
            if speed <= 0:
                self._stop(ac, StopReason.BLOCKED_TRAFFIC)
                return
            max_dist = edge.length_m
            if self._node_occupied_by_other(pos.edge_b, ac.callsign):
                max_dist = edge.length_m - NODE_STOP_SHORT_M
            leader_limit = self._leader_gap_limit(ac)
            if leader_limit is not None:
                max_dist = min(max_dist, leader_limit)
            advance = min(speed * time_left, max_dist - pos.dist_m)
            if advance <= 1e-9:
                self._stop(ac, StopReason.BLOCKED_TRAFFIC)
                return
            pos.dist_m += advance
            time_left -= advance / speed
            ac.speed_ms = speed
            moved = True
            if self.weather.taxi_speed_factor < 1.0:
                ac.add_delay(DelayCause.WEATHER,
                             (advance / speed) *
                             (1.0 - self.weather.taxi_speed_factor))
            if pos.dist_m >= edge.length_m - 1e-6:
                arrived = pos.edge_b
                ac.position = Position.at_node(arrived)
                cl.taxi_route.pop(0)
                # Keep the issued route in sync as ground truth progresses.
                iss = self.issued[ac.callsign]
                if iss.taxi_route and iss.taxi_route[0] == arrived:
                    iss.taxi_route.pop(0)
                self._on_node_arrival(ac, arrived)
                if ac.state not in (AircraftState.TAXI_OUT,
                                    AircraftState.TAXI_IN,
                                    AircraftState.HOLD_SHORT):
                    return
        if moved:
            self._stopped_reason.pop(ac.callsign, None)

    def _maybe_hold_short_state(self, ac: Aircraft, node_id: str) -> None:
        """Departures stopped at the hold point for their runway get the
        HOLD_SHORT state (and will call ready for departure)."""
        if ac.plan.kind != FlightKind.DEPARTURE or \
                ac.state != AircraftState.TAXI_OUT:
            return
        rw = self.airport.runway_for_end(ac.plan.runway)
        if rw is None:
            return
        threshold = rw.threshold(ac.plan.runway)
        if self.airport.hold_short_runway(node_id) == rw.id and \
                self.airport.edge_between(node_id, threshold) is not None:
            if ac.taxi_started_s is not None and ac.taxi_ended_s is None:
                ac.taxi_ended_s = self.t
                ac.actual_taxi_time_s = ac.taxi_ended_s - ac.taxi_started_s
            self._transition(ac, AircraftState.HOLD_SHORT)
            # A runway clearance issued in advance is consumed on arrival.
            self._maybe_enter_runway(ac)

    def _on_node_arrival(self, ac: Aircraft, node_id: str) -> None:
        cl = ac.clearances
        iss = self.issued[ac.callsign]
        # Crossing complete? Consume the clearance only once the aircraft has
        # actually entered and then cleared the runway.
        started = self._crossing_started.get(ac.callsign, set())
        touching = self.airport.runways_touching_position(ac.position)
        for rw_id in list(started):
            if rw_id not in touching:
                cl.cross_runways.discard(rw_id)
                iss.cross_runways.discard(rw_id)
                started.discard(rw_id)

        node = self.airport.nodes[node_id]

        # Entering the runway threshold under a runway clearance?
        if cl.runway_clearance in (ClearanceType.TAKEOFF,
                                   ClearanceType.LINE_UP_AND_WAIT) and \
                cl.runway_clearance_end is not None:
            rw = self.airport.runway_for_end(cl.runway_clearance_end)
            if rw is not None and \
                    rw.threshold(cl.runway_clearance_end) == node_id:
                self._lineup_end[ac.callsign] = cl.runway_clearance_end
                if ac.taxi_started_s is not None and ac.taxi_ended_s is None:
                    ac.taxi_ended_s = self.t
                    ac.actual_taxi_time_s = (
                        ac.taxi_ended_s - ac.taxi_started_s)
                self._transition(ac, AircraftState.LINE_UP_WAIT)
                ac.heading_deg = rw.headings[cl.runway_clearance_end]
                cl.taxi_route = []
                return

        if not cl.taxi_route:
            # Destination reached.
            if ac.state == AircraftState.TAXI_IN and \
                    node.type == NodeType.GATE:
                if ac.taxi_started_s is not None and ac.taxi_ended_s is None:
                    ac.taxi_ended_s = self.t
                    ac.actual_taxi_time_s = (
                        ac.taxi_ended_s - ac.taxi_started_s)
                ac.actual_ongate_s = self.t
                self._transition(ac, AircraftState.AT_GATE)
                self.emit(EventType.PILOT_READBACK,
                          f"{ac.callsign} on the gate.",
                          callsign=ac.callsign, to_controller=False)
                return
            if ac.state == AircraftState.TAXI_IN:
                self._emit_request(ac, "taxi_in", force=True)
                return
            if ac.state == AircraftState.TAXI_OUT:
                self._maybe_hold_short_state(ac, node_id)
                if ac.state == AircraftState.TAXI_OUT:
                    # Stopped short of anywhere useful; ask for more.
                    self._emit_request(ac, "taxi", force=True)

    # -- runway ops ------------------------------------------------------

    def _runway_position(self, rw: Runway, end: str, dist: float) -> Position:
        ordered = rw.nodes if rw.nodes[0] == rw.threshold(end) else \
            list(reversed(rw.nodes))
        remaining = dist
        for a, b in zip(ordered, ordered[1:]):
            e = self.airport.edge_between(a, b)
            if remaining <= e.length_m:
                return Position.on_edge(a, b, remaining)
            remaining -= e.length_m
        return Position.at_node(ordered[-1])

    def _advance_line_up_wait(self, ac: Aircraft) -> None:
        cl = ac.clearances
        if cl.runway_clearance == ClearanceType.TAKEOFF:
            end = self._lineup_end.get(ac.callsign,
                                       cl.runway_clearance_end or
                                       ac.plan.runway)
            self._roll_dist[ac.callsign] = 0.0
            self._roll_speed[ac.callsign] = 0.0
            self._transition(ac, AircraftState.TAKEOFF_ROLL)
            ac.heading_deg = self.airport.runway_for_end(end).headings[end]
        else:
            self._stop(ac, StopReason.LINE_UP_WAIT)

    def _advance_takeoff_roll(self, ac: Aircraft) -> None:
        end = self._lineup_end.get(ac.callsign,
                                   ac.clearances.runway_clearance_end or
                                   ac.plan.runway)
        rw = self.airport.runway_for_end(end)
        v = self._roll_speed[ac.callsign] + \
            ac.profile.takeoff_accel_ms2 * self.tick_s
        d = self._roll_dist[ac.callsign] + v * self.tick_s
        self._roll_speed[ac.callsign] = v
        self._roll_dist[ac.callsign] = d
        ac.speed_ms = v
        if v >= ac.profile.rotate_speed_ms or d >= rw.length_m:
            # Liftoff.
            ac.actual_takeoff_s = self.t
            self.last_runway_op[rw.id] = (self.t, ac.wake)
            self.completed_takeoffs += 1
            lift = self._runway_position(rw, end, min(d, rw.length_m))
            lift.air_dist_m = 0.0
            ac.position = lift
            self._transition(ac, AircraftState.DEPARTED)
            self.emit(EventType.STATE_TRANSITION,
                      f"{ac.callsign} airborne runway {end}.",
                      callsign=ac.callsign,
                      data={"runway": end}, to_controller=False)
        else:
            ac.position = self._runway_position(rw, end, d)
            ac.position.air_dist_m = None

    def _advance_departed(self, ac: Aircraft) -> None:
        if ac.frequency == Frequency.DEPARTURE:
            return  # handed off; no longer tracked
        climb = ac.profile.rotate_speed_ms * LIFTOFF_CLIMB_FACTOR
        ac.position.air_dist_m = (ac.position.air_dist_m or 0.0) + \
            climb * self.tick_s
        ac.speed_ms = climb
        if ac.position.air_dist_m > DEPARTED_REMOVE_DIST_M:
            ac.frequency = Frequency.DEPARTURE
            self.emit(EventType.SYSTEM,
                      f"{ac.callsign} handed off to departure.",
                      callsign=ac.callsign, to_controller=False)

    def _go_around(self, ac: Aircraft, reason: str, severity: Severity,
                   atc_fault: bool) -> None:
        ac.go_arounds += 1
        ac.atc_fault_go_around = atc_fault
        ac.position.air_dist_m = ac.profile.approach_speed_ms * \
            GO_AROUND_LAP_S
        for cl in (ac.clearances, self.issued[ac.callsign]):
            cl.runway_clearance = None
            cl.runway_clearance_end = None
        ac.pending_request = "landing"
        self._request_last_emit.pop(ac.callsign, None)
        self.emit(EventType.INCIDENT,
                  f"{ac.callsign} going around: {reason}",
                  callsign=ac.callsign,
                  data={"reason": reason},
                  severity=severity, incident=IncidentType.GO_AROUND)

    def _advance_arriving(self, ac: Aircraft) -> None:
        if ac.callsign in self._force_go_around:
            self._force_go_around.discard(ac.callsign)
            self._go_around(ac, "traffic on final (scenario inject)",
                            Severity.MAJOR, atc_fault=False)
            return
        ac.position.air_dist_m = (ac.position.air_dist_m or 0.0) - \
            ac.profile.approach_speed_ms * self.tick_s
        ac.speed_ms = ac.profile.approach_speed_ms
        if self.t > ac.plan.scheduled_time_s:
            ac.add_delay(
                attribute(ac, StopReason.AIRBORNE_QUEUE, self.t, None, False),
                self.tick_s)
        if ac.position.air_dist_m > 0:
            return
        # At the threshold: land or go around.
        end = ac.plan.runway
        rw = self.airport.runway_for_end(end)
        issued = self.issued[ac.callsign]
        cleared = (issued.runway_clearance == ClearanceType.LAND and
                   issued.runway_clearance_end == end)
        occupants = self.runway_physically_occupied_by(rw.id, ac.callsign)
        # Static traffic on an intersecting runway is safe to land over;
        # only rolling operations on shared pavement force a go-around.
        blockers = [
            cs for cs in self.runway_operation_blockers(rw.id, ac.callsign)
            if self.aircraft[cs].state in (AircraftState.TAKEOFF_ROLL,
                                           AircraftState.LANDING_ROLL)
            and cs not in occupants
        ]
        if cleared and not occupants and not blockers:
            ac.actual_touchdown_s = self.t
            self.last_runway_op[rw.id] = (self.t, ac.wake)
            self.completed_landings += 1
            v = ac.profile.approach_speed_ms * 0.95
            self._roll_speed[ac.callsign] = v
            self._roll_dist[ac.callsign] = 0.0
            self._exit_plan[ac.callsign] = self._choose_exit(ac, rw, end, v)
            pos = self._runway_position(rw, end, 0.0)
            ac.position = pos
            ac.heading_deg = rw.headings[end]
            self._transition(ac, AircraftState.LANDING_ROLL,
                             scheduled_s=ac.plan.scheduled_time_s)
            return
        if cleared and (occupants or blockers):
            who = ", ".join(occupants + blockers)
            self.emit(EventType.INCIDENT,
                      f"{ac.callsign} cleared to land runway {end} with "
                      f"{who} on the runway",
                      callsign=ac.callsign,
                      data={"runway": rw.id, "conflicting": who},
                      severity=Severity.CRITICAL,
                      incident=IncidentType.NEAR_MISS)
            self._go_around(ac, f"runway {end} occupied", Severity.MAJOR,
                            atc_fault=True)
            return
        self._go_around(ac, "no landing clearance", Severity.MINOR,
                        atc_fault=False)

    def _choose_exit(self, ac: Aircraft, rw: Runway, end: str,
                     touchdown_speed: float) -> tuple[float, str, str]:
        stop_dist = (touchdown_speed ** 2 - ac.profile.taxi_speed_ms ** 2) / \
            (2.0 * ac.profile.landing_decel_ms2)
        exits = self.airport.runway_exits(end)
        candidates = [e for e in exits if e[0] >= stop_dist - 1.0]
        if not candidates:
            candidates = exits[-1:]
        best_dist = candidates[0][0]
        at_first = [e for e in candidates if abs(e[0] - best_dist) < 1.0]
        if len(at_first) == 1:
            return at_first[0]
        # Tie (e.g. exits on both sides): prefer the side closer to the gate.
        def path_len(e: tuple[float, str, str]) -> float:
            p = self.airport.shortest_path(e[2], ac.plan.gate)
            return self.airport.route_length_m(p) if p else float("inf")
        return min(at_first, key=path_len)

    def _advance_landing_roll(self, ac: Aircraft) -> None:
        end = ac.plan.runway
        rw = self.airport.runway_for_end(end)
        v = max(ac.profile.taxi_speed_ms,
                self._roll_speed[ac.callsign] -
                ac.profile.landing_decel_ms2 * self.tick_s)
        d = min(self._roll_dist[ac.callsign] + v * self.tick_s, rw.length_m)
        self._roll_speed[ac.callsign] = v
        self._roll_dist[ac.callsign] = d
        ac.speed_ms = v
        exit_dist, exit_rw_node, exit_off_node = self._exit_plan[ac.callsign]
        if d >= exit_dist and v <= ac.profile.taxi_speed_ms * 1.05:
            ac.position = Position.at_node(exit_rw_node)
            ac.clearances.taxi_route = [exit_off_node]
            self.issued[ac.callsign].taxi_route = [exit_off_node]
            ac.taxi_started_s = self.t
            self._transition(ac, AircraftState.TAXI_IN)
            self._set_min_taxi_benchmark_arrival(ac, exit_off_node)
        else:
            ac.position = self._runway_position(rw, end, d)

    def _set_min_taxi_benchmark_arrival(self, ac: Aircraft,
                                        from_node: str) -> None:
        path = self.airport.shortest_path(from_node, ac.plan.gate)
        if path is not None and len(path) >= 2:
            ac.min_taxi_time_s = self.airport.min_taxi_time_s(
                path, ac.profile.taxi_speed_ms)

    def _advance_pushback(self, ac: Aircraft) -> None:
        done_at = self._pushback_done_at.get(ac.callsign, self.t)
        if self.t < done_at:
            return
        gate_node = ac.plan.gate
        ramp_edges = self.airport.adjacency.get(gate_node, [])
        if not ramp_edges:
            return
        ramp_node = ramp_edges[0].other(gate_node)
        if self._node_occupied_by_other(ramp_node, ac.callsign):
            self._stop(ac, StopReason.BLOCKED_TRAFFIC)
            return
        ac.position = Position.at_node(ramp_node)
        self._transition(ac, AircraftState.TAXI_OUT)
        ac.pending_request = "taxi"
        self._emit_request(ac, "taxi", force=True)

    def _advance_aircraft(self) -> None:
        for cs in self.order:
            ac = self.aircraft[cs]
            state = ac.state
            if state == AircraftState.SCHEDULED:
                if self.t > ac.plan.scheduled_time_s and \
                        not ac.clearances.pushback_approved:
                    ac.add_delay(attribute(
                        ac, StopReason.GATE_WAIT, self.t, NodeType.GATE,
                        False), self.tick_s)
            elif state == AircraftState.PUSHBACK:
                self._advance_pushback(ac)
            elif state in (AircraftState.TAXI_OUT, AircraftState.TAXI_IN,
                           AircraftState.HOLD_SHORT):
                self._stopped_reason.pop(cs, None)
                self._advance_taxiing(ac)
            elif state == AircraftState.LINE_UP_WAIT:
                self._advance_line_up_wait(ac)
            elif state == AircraftState.TAKEOFF_ROLL:
                self._advance_takeoff_roll(ac)
            elif state == AircraftState.DEPARTED:
                self._advance_departed(ac)
            elif state == AircraftState.ARRIVING:
                self._advance_arriving(ac)
            elif state == AircraftState.LANDING_ROLL:
                self._advance_landing_roll(ac)

    # ------------------------------------------------------------------
    # Delay attribution for stopped aircraft
    # ------------------------------------------------------------------

    def _traffic_within(self, ac: Aircraft, radius_m: float) -> bool:
        for cs in self.order:
            if cs == ac.callsign:
                continue
            other = self.aircraft[cs]
            if not self._ground_active(other):
                continue
            if self.airport.ground_distance(ac.position,
                                            other.position) <= radius_m:
                return True
        return False

    def _attribute_stopped_delays(self) -> None:
        from .delays import JUSTIFIED_HOLD_RADIUS_M
        for cs, reason in self._stopped_reason.items():
            ac = self.aircraft.get(cs)
            if ac is None:
                continue
            node_type = None
            if ac.position.node is not None:
                node_type = self.airport.nodes[ac.position.node].type
            traffic = False
            if reason == StopReason.ATC_HOLD:
                traffic = self._traffic_within(ac, JUSTIFIED_HOLD_RADIUS_M)
            ac.add_delay(
                attribute(ac, reason, self.t, node_type, traffic),
                self.tick_s)

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def _cooldown_ok(self, key: str, window_s: int = 120) -> bool:
        last = self._incident_cooldown.get(key)
        if last is not None and self.t - last < window_s:
            return False
        self._incident_cooldown[key] = self.t
        return True

    def _runway_authorized(self, ac: Aircraft, rw: Runway) -> bool:
        issued = self.issued[ac.callsign]
        if issued.cross_runways and rw.id in issued.cross_runways:
            return True
        if issued.runway_clearance is not None and \
                issued.runway_clearance_end in rw.ends:
            if ac.state == AircraftState.TAKEOFF_ROLL:
                return issued.runway_clearance == ClearanceType.TAKEOFF
            if ac.state in (AircraftState.LINE_UP_WAIT,
                            AircraftState.TAXI_OUT,
                            AircraftState.HOLD_SHORT):
                return issued.runway_clearance in (
                    ClearanceType.TAKEOFF, ClearanceType.LINE_UP_AND_WAIT)
        if ac.state in (AircraftState.LANDING_ROLL, AircraftState.TAXI_IN):
            own = self.airport.runway_for_end(ac.plan.runway)
            if own is not None and rw.id == own.id:
                return True
        # An authorized operation on an intersecting runway covers the
        # shared pavement (e.g. a takeoff roll through the intersection).
        op_rw = self._operation_runway(ac)
        if op_rw is not None and op_rw.id != rw.id and \
                op_rw.node_set & rw.node_set and \
                self._runway_authorized(ac, op_rw):
            return True
        return False

    def _detect_conflicts(self) -> None:
        ground = [self.aircraft[cs] for cs in self.order
                  if self._ground_active(self.aircraft[cs])]
        # Pairwise proximity.
        for i in range(len(ground)):
            for j in range(i + 1, len(ground)):
                a, b = ground[i], ground[j]
                d = self.airport.ground_distance(a.position, b.position)
                pair = f"{a.callsign}|{b.callsign}"
                if d < COLLISION_M:
                    if self._cooldown_ok(f"col:{pair}"):
                        self.emit(
                            EventType.INCIDENT,
                            f"COLLISION: {a.callsign} and {b.callsign} "
                            f"({d:.0f} m apart)",
                            data={"pair": [a.callsign, b.callsign],
                                  "distance_m": round(d, 1)},
                            severity=Severity.CRITICAL,
                            incident=IncidentType.COLLISION)
                elif d < NEAR_MISS_M and \
                        (a.speed_ms > 0.5 or b.speed_ms > 0.5):
                    if self._cooldown_ok(f"nm:{pair}"):
                        self.emit(
                            EventType.INCIDENT,
                            f"NEAR MISS: {a.callsign} and {b.callsign} "
                            f"{d:.0f} m apart",
                            data={"pair": [a.callsign, b.callsign],
                                  "distance_m": round(d, 1)},
                            severity=Severity.MAJOR,
                            incident=IncidentType.NEAR_MISS)
                # Head-to-head on the same edge: deadlock.
                pa, pb = a.position, b.position
                if pa.edge_a is not None and pb.edge_a is not None and \
                        pa.edge_a == pb.edge_b and pa.edge_b == pb.edge_a:
                    if self._cooldown_ok(f"dl:{pair}", window_s=300):
                        self.emit(
                            EventType.CONFLICT_ALERT,
                            f"DEADLOCK: {a.callsign} and {b.callsign} are "
                            f"nose-to-nose between {pa.edge_a} and "
                            f"{pa.edge_b}; one must be re-routed",
                            data={"pair": [a.callsign, b.callsign]},
                            severity=Severity.MAJOR,
                            incident=IncidentType.TAXI_DEADLOCK)

        # Runway occupancy authorization + multi-occupancy.
        for rw in self.airport.runways.values():
            occupants: list[Aircraft] = []
            for ac in ground:
                if rw.id in self.airport.runways_touching_position(
                        ac.position):
                    occupants.append(ac)
                    if not self._runway_authorized(ac, rw):
                        if self._cooldown_ok(f"inc:{ac.callsign}:{rw.id}",
                                             window_s=60):
                            self.emit(
                                EventType.INCIDENT,
                                f"RUNWAY INCURSION: {ac.callsign} on runway "
                                f"{rw.id} without clearance",
                                callsign=ac.callsign,
                                data={"runway": rw.id},
                                severity=Severity.CRITICAL,
                                incident=IncidentType.RUNWAY_INCURSION)
            rolling = [a for a in occupants if a.state in
                       (AircraftState.TAKEOFF_ROLL,
                        AircraftState.LANDING_ROLL)]
            if rolling and len(occupants) > 1:
                names = sorted(a.callsign for a in occupants)
                if self._cooldown_ok(f"rwc:{rw.id}:{'|'.join(names)}"):
                    self.emit(
                        EventType.INCIDENT,
                        f"RUNWAY CONFLICT: {', '.join(names)} "
                        f"simultaneously on runway {rw.id} during an active "
                        "operation",
                        data={"runway": rw.id, "aircraft": names},
                        severity=Severity.CRITICAL,
                        incident=IncidentType.NEAR_MISS)

        # Arrival short-final alerts.
        for cs in self.order:
            ac = self.aircraft[cs]
            if ac.state != AircraftState.ARRIVING:
                continue
            eta = (ac.position.air_dist_m or 0.0) / \
                ac.profile.approach_speed_ms
            if eta > ARRIVAL_ALERT_S:
                continue
            rw = self.airport.runway_for_end(ac.plan.runway)
            if rw is None:
                continue
            occ = self.runway_physically_occupied_by(rw.id, cs)
            blk = self.runway_operation_blockers(rw.id, cs)
            if occ or blk:
                if self._cooldown_ok(f"final:{cs}", window_s=30):
                    self.emit(
                        EventType.CONFLICT_ALERT,
                        f"ALERT: {cs} is {eta:.0f}s from runway "
                        f"{ac.plan.runway} and the runway is not clear "
                        f"({', '.join(occ + blk)})",
                        callsign=cs,
                        data={"runway": rw.id, "eta_s": round(eta),
                              "conflicting": occ + blk},
                        severity=Severity.MAJOR)

    # ------------------------------------------------------------------
    # Scenario events
    # ------------------------------------------------------------------

    def _apply_scenario_events(self) -> None:
        while self._pending_events and self._pending_events[0].at_s <= self.t:
            ev = self._pending_events.pop(0)
            self._apply_scenario_event(ev)

    def _apply_scenario_event(self, ev: ScenarioEvent) -> None:
        p = ev.params
        if ev.kind == "weather_change":
            for k, v in p.items():
                if hasattr(self.weather, k):
                    setattr(self.weather, k, v)
            self.emit(EventType.SYSTEM,
                      ev.announce or f"Weather update: "
                      f"{self.weather.describe()}.",
                      data={"kind": ev.kind, **p})
        elif ev.kind == "runway_change":
            self.active_ends = set(p.get("active_ends", self.active_ends))
            reassign: dict[str, str] = p.get("reassign_arrivals", {})
            for cs in self.order:
                ac = self.aircraft[cs]
                new_end = reassign.get(ac.plan.runway)
                if new_end is None:
                    continue
                if ac.state == AircraftState.ARRIVING:
                    eta = (ac.position.air_dist_m or 0.0) / \
                        ac.profile.approach_speed_ms
                    if eta > 120:
                        ac.plan.runway = new_end
                        for cl in (ac.clearances, self.issued[cs]):
                            cl.runway_clearance = None
                            cl.runway_clearance_end = None
                        ac.pending_request = "landing"
                elif ac.state in (AircraftState.SCHEDULED,
                                  AircraftState.PUSHBACK,
                                  AircraftState.TAXI_OUT,
                                  AircraftState.HOLD_SHORT):
                    ac.plan.runway = new_end
            self.emit(EventType.SYSTEM,
                      ev.announce or "Runway configuration change: active "
                      f"runways now {sorted(self.active_ends)}.",
                      data={"kind": ev.kind, **p})
        elif ev.kind == "runway_closure":
            self.closed_runways.add(p["runway_id"])
            self.emit(EventType.SYSTEM,
                      ev.announce or f"Runway {p['runway_id']} is CLOSED.",
                      data={"kind": ev.kind, **p})
        elif ev.kind == "runway_reopen":
            self.closed_runways.discard(p["runway_id"])
            self.emit(EventType.SYSTEM,
                      ev.announce or f"Runway {p['runway_id']} is open.",
                      data={"kind": ev.kind, **p})
        elif ev.kind == "emergency":
            ac = self.aircraft.get(p["callsign"])
            if ac is not None:
                ac.emergency = True
                self._request_last_emit.pop(ac.callsign, None)
                if ac.state == AircraftState.ARRIVING:
                    self._emit_request(ac, "landing", force=True)
                self.emit(EventType.SYSTEM,
                          ev.announce or
                          f"{ac.callsign} has declared an EMERGENCY and "
                          "requires priority handling.",
                          callsign=ac.callsign,
                          data={"kind": ev.kind, **p},
                          severity=Severity.MAJOR)
        elif ev.kind == "go_around":
            cs = p.get("callsign")
            if cs in self.aircraft and \
                    self.aircraft[cs].state == AircraftState.ARRIVING:
                self._force_go_around.add(cs)

    # ------------------------------------------------------------------
    # Controller cadence
    # ------------------------------------------------------------------

    CONTROLLER_TRIGGER_TYPES = {
        EventType.PILOT_REQUEST,
        EventType.PILOT_UNABLE,
        EventType.INSTRUCTION_REJECTED,
        EventType.CONFLICT_ALERT,
        EventType.INCIDENT,
        EventType.SYSTEM,
    }

    def _should_call_controller(self) -> bool:
        if self.controller is None:
            return False
        if any(e.type in self.CONTROLLER_TRIGGER_TYPES
               for e in self.controller_outbox):
            return True
        if self.t - self._last_scan_t >= self.scenario.scan_interval_s and \
                any(self._ground_active(self.aircraft[cs]) or
                    self.aircraft[cs].state in (AircraftState.SCHEDULED,
                                                AircraftState.ARRIVING)
                    for cs in self.order):
            return True
        return False

    def _call_controller(self) -> None:
        assert self.controller is not None
        self._last_scan_t = self.t
        outbox = list(self.controller_outbox)
        self.controller_outbox.clear()
        view = self.controller_view(outbox)
        started = time.perf_counter()
        instructions = self.controller.decide(self, view, outbox)
        latency_ms = (time.perf_counter() - started) * 1000.0
        self.decision_log.append({
            "t": self.t,
            "latency_ms": round(latency_ms, 1),
            "n_instructions": len(instructions),
        })
        if not getattr(self.controller, "self_applying", False):
            for instr in instructions:
                self.apply_instruction(instr)

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def _describe_position(self, ac: Aircraft) -> str:
        pos = ac.position
        if ac.state == AircraftState.ARRIVING:
            eta = (pos.air_dist_m or 0.0) / ac.profile.approach_speed_ms
            return (f"{(pos.air_dist_m or 0.0) / 1852:.1f} nm final RWY "
                    f"{ac.plan.runway}, ~{eta:.0f}s out")
        if ac.state == AircraftState.DEPARTED:
            return "airborne, departing"
        if pos.node is not None:
            n = self.airport.nodes[pos.node]
            return f"at {pos.node} ({n.type.value})"
        edge = self.airport.edge_between(pos.edge_a, pos.edge_b)
        pct = 0 if edge is None else round(100 * pos.dist_m / edge.length_m)
        return f"on {edge.name if edge else '?'} {pos.edge_a}->{pos.edge_b} ({pct}%)"

    def controller_view(self, outbox: Optional[list[Event]] = None) -> dict:
        """Compact, LLM-facing state snapshot."""
        aircraft = []
        for cs in self.order:
            ac = self.aircraft[cs]
            if ac.state == AircraftState.AT_GATE and \
                    ac.plan.kind == FlightKind.ARRIVAL:
                continue
            if ac.state == AircraftState.DEPARTED and \
                    ac.frequency == Frequency.DEPARTURE:
                continue
            issued = self.issued[cs]
            entry = {
                "callsign": cs,
                "type": ac.profile.type_code,
                "wake": ac.wake.value,
                "kind": ac.plan.kind.value,
                "state": ac.state.value,
                "frequency": ac.frequency.value,
                "position": self._describe_position(ac),
                "gate": ac.plan.gate,
                "runway": ac.plan.runway,
                "scheduled_time_s": ac.plan.scheduled_time_s,
                "pending_request": ac.pending_request,
                "route_remaining": list(ac.clearances.taxi_route),
                "cleared": {
                    "crossings": sorted(issued.cross_runways),
                    "runway": issued.runway_clearance.value
                    if issued.runway_clearance else None,
                    "runway_end": issued.runway_clearance_end,
                    "hold_short_at": issued.hold_short_at,
                    "holding_position": issued.holding_position,
                },
                "total_delay_s": round(ac.total_delay_s),
            }
            if ac.emergency:
                entry["emergency"] = True
            aircraft.append(entry)

        runway_status = {}
        for rw in self.airport.runways.values():
            occ = self.runway_physically_occupied_by(rw.id, exclude="")
            last = self.seconds_since_last_runway_op(rw.id)
            runway_status[rw.id] = {
                "closed": rw.id in self.closed_runways,
                "occupied_by": occ,
                "seconds_since_last_op": None if last is None else
                round(last[0]),
                "last_op_wake": None if last is None else last[1].value,
            }

        return {
            "t_s": self.t,
            "weather": {
                "text": self.weather.describe(),
                "taxi_speed_factor": self.weather.taxi_speed_factor,
                "separation_multiplier": self.weather.separation_multiplier,
            },
            "active_runway_ends": sorted(self.active_ends),
            "closed_runways": sorted(self.closed_runways),
            "runways": runway_status,
            "aircraft": aircraft,
            "new_events": [
                {"t_s": e.t_s, "type": e.type.value, "callsign": e.callsign,
                 "text": e.text,
                 **({"severity": e.severity.value} if e.severity else {})}
                for e in (outbox or [])
            ],
        }

    def ui_snapshot(self) -> dict:
        """Full-fidelity snapshot for the UI (positions in meters)."""
        aircraft = []
        for cs in self.order:
            ac = self.aircraft[cs]
            if ac.state == AircraftState.DEPARTED and \
                    ac.frequency == Frequency.DEPARTURE:
                continue  # gone; keep the map clean
            if ac.position.node is not None or \
                    ac.position.edge_a is not None:
                x, y = self.airport.xy_of(ac.position)
            else:
                x, y = 0.0, 0.0  # pure-airborne; set by the branch below
            if ac.position.is_airborne and ac.state in (
                    AircraftState.ARRIVING, AircraftState.DEPARTED):
                # Offset along the runway heading for a ground track.
                import math as _m
                rw = self.airport.runway_for_end(ac.plan.runway)
                if rw is not None:
                    hdg = _m.radians(rw.headings[ac.plan.runway])
                    d = ac.position.air_dist_m or 0.0
                    if ac.state == AircraftState.ARRIVING:
                        thr = self.airport.nodes[rw.threshold(ac.plan.runway)]
                        x = thr.x - _m.sin(hdg) * d
                        y = thr.y - _m.cos(hdg) * d
                        ac.heading_deg = rw.headings[ac.plan.runway]
                    else:
                        x = x + _m.sin(_m.radians(ac.heading_deg)) * d
                        y = y + _m.cos(_m.radians(ac.heading_deg)) * d
            aircraft.append({
                "callsign": cs,
                "type": ac.profile.type_code,
                "wake": ac.wake.value,
                "kind": ac.plan.kind.value,
                "state": ac.state.value,
                "x": round(x, 1), "y": round(y, 1),
                "heading_deg": round(ac.heading_deg, 1),
                "speed_kt": round(ac.speed_ms / 0.514444, 1),
                "frequency": ac.frequency.value,
                "gate": ac.plan.gate,
                "runway": ac.plan.runway,
                "scheduled_time_s": ac.plan.scheduled_time_s,
                "emergency": ac.emergency,
                "pending_request": ac.pending_request,
                "total_delay_s": round(ac.total_delay_s),
                "delay_by_cause": {k: round(v)
                                   for k, v in ac.delay_by_cause.items()},
                "min_taxi_time_s": round(ac.min_taxi_time_s)
                if ac.min_taxi_time_s else None,
                "actual_taxi_time_s": round(ac.actual_taxi_time_s),
                "go_arounds": ac.go_arounds,
                "airborne": ac.position.is_airborne,
            })
        return {
            "t_s": self.t,
            "aircraft": aircraft,
            "active_runway_ends": sorted(self.active_ends),
            "closed_runways": sorted(self.closed_runways),
            "weather": self.weather.model_dump(),
        }

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def step(self) -> None:
        self.t += self.tick_s
        self._stopped_reason.clear()
        self._apply_scenario_events()
        self._spawn_due_aircraft()
        self._process_delayed_comms()
        self._generate_pilot_requests()
        self._advance_aircraft()
        self._detect_conflicts()
        self._attribute_stopped_delays()
        if self._should_call_controller():
            self._call_controller()

    def run(self, duration_s: Optional[int] = None) -> None:
        end = self.t + (duration_s if duration_s is not None
                        else self.scenario.duration_s)
        while self.t < end:
            self.step()

    @property
    def all_complete(self) -> bool:
        """Every scheduled aircraft has finished its ground operation."""
        if len(self.aircraft) < len(self.scenario.traffic):
            return False
        for ac in self.aircraft.values():
            if ac.plan.kind == FlightKind.DEPARTURE:
                if ac.state != AircraftState.DEPARTED:
                    return False
            elif ac.state != AircraftState.AT_GATE:
                return False
        return True


def entry_to_plan(entry) -> "FlightPlan":
    from .models import FlightPlan
    return FlightPlan(
        callsign=entry.callsign,
        type_code=entry.type_code,
        kind=entry.kind,
        gate=entry.gate,
        runway=entry.runway,
        scheduled_time_s=entry.scheduled_time_s,
        origin=entry.origin,
        destination=entry.destination,
    )
