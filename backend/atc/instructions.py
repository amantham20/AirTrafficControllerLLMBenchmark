"""Controller instruction schema and the validation layer.

Instructions mirror the Anthropic tool-use schema one-to-one: the LLM emits
tool calls, the adapter parses them into these models, and every instruction
— scripted or LLM — passes through `Validator` before the engine applies it.
Rejections carry a severity so bad instructions become scoring signal.
"""

from __future__ import annotations

import enum
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

from .airport import Airport
from .models import (
    Aircraft,
    AircraftState,
    ClearanceType,
    FlightKind,
    Frequency,
    Severity,
    WakeCategory,
)

# ---------------------------------------------------------------------------
# Instruction models (one per controller tool)
# ---------------------------------------------------------------------------


class ApprovePushback(BaseModel):
    kind: Literal["approve_pushback"] = "approve_pushback"
    callsign: str


class TaxiInstruction(BaseModel):
    kind: Literal["taxi_instruction"] = "taxi_instruction"
    callsign: str
    route: list[str]
    hold_short_at: Optional[str] = None


class RunwayClearance(BaseModel):
    kind: Literal["runway_clearance"] = "runway_clearance"
    callsign: str
    runway: str  # runway end, e.g. "05"
    clearance_type: ClearanceType


class HoldPosition(BaseModel):
    kind: Literal["hold_position"] = "hold_position"
    callsign: str


class ResumeTaxi(BaseModel):
    kind: Literal["resume_taxi"] = "resume_taxi"
    callsign: str


class CrossRunway(BaseModel):
    kind: Literal["cross_runway"] = "cross_runway"
    callsign: str
    runway: str  # runway id "14/32" or either end name "14"/"32"


class ContactNextFrequency(BaseModel):
    kind: Literal["contact_next_frequency"] = "contact_next_frequency"
    callsign: str


Instruction = Union[
    ApprovePushback,
    TaxiInstruction,
    RunwayClearance,
    HoldPosition,
    ResumeTaxi,
    CrossRunway,
    ContactNextFrequency,
]


class InstructionEnvelope(BaseModel):
    """Helper for parsing an instruction from a dict via the discriminator."""

    instruction: Instruction = Field(discriminator="kind")


def parse_instruction(data: dict) -> Instruction:
    return InstructionEnvelope(instruction=data).instruction


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class RejectCode(str, enum.Enum):
    UNKNOWN_CALLSIGN = "UNKNOWN_CALLSIGN"
    BAD_STATE = "BAD_STATE"
    WRONG_FREQUENCY = "WRONG_FREQUENCY"
    ROUTE_EMPTY = "ROUTE_EMPTY"
    ROUTE_DISCONNECTED = "ROUTE_DISCONNECTED"
    ROUTE_NOT_FROM_POSITION = "ROUTE_NOT_FROM_POSITION"
    ROUTE_ALONG_RUNWAY = "ROUTE_ALONG_RUNWAY"
    HOLD_POINT_NOT_ON_ROUTE = "HOLD_POINT_NOT_ON_ROUTE"
    UNKNOWN_RUNWAY = "UNKNOWN_RUNWAY"
    RUNWAY_CLOSED = "RUNWAY_CLOSED"
    INACTIVE_RUNWAY = "INACTIVE_RUNWAY"
    NOT_AT_RUNWAY_HOLD_POINT = "NOT_AT_RUNWAY_HOLD_POINT"
    RUNWAY_OCCUPIED = "RUNWAY_OCCUPIED"
    ARRIVAL_ON_SHORT_FINAL = "ARRIVAL_ON_SHORT_FINAL"
    WAKE_SEPARATION = "WAKE_SEPARATION"
    WRONG_APPROACH = "WRONG_APPROACH"
    NO_CROSSING_AHEAD = "NO_CROSSING_AHEAD"
    CROSSING_UNSAFE = "CROSSING_UNSAFE"
    NO_NEXT_FREQUENCY = "NO_NEXT_FREQUENCY"
    NOTHING_TO_RESUME = "NOTHING_TO_RESUME"


class ValidationResult(BaseModel):
    ok: bool
    code: Optional[RejectCode] = None
    reason: str = ""
    severity: Optional[Severity] = None

    @classmethod
    def accept(cls) -> "ValidationResult":
        return cls(ok=True)

    @classmethod
    def reject(cls, code: RejectCode, reason: str,
               severity: Severity) -> "ValidationResult":
        return cls(ok=False, code=code, reason=reason, severity=severity)


# Wake turbulence: minimum seconds behind the preceding operation on the same
# (or an intersecting) runway, keyed (leading, trailing).
WAKE_INTERVAL_S: dict[tuple[WakeCategory, WakeCategory], int] = {
    (WakeCategory.HEAVY, WakeCategory.HEAVY): 90,
    (WakeCategory.HEAVY, WakeCategory.MEDIUM): 120,
    (WakeCategory.HEAVY, WakeCategory.LIGHT): 180,
    (WakeCategory.MEDIUM, WakeCategory.LIGHT): 120,
}
DEFAULT_WAKE_INTERVAL_S = 60

# An arrival closer than this to the threshold blocks takeoff clearances on
# that runway; closer than the CRITICAL threshold makes the attempt critical.
SHORT_FINAL_S = 100
SHORT_FINAL_CRITICAL_S = 45


def wake_interval_s(leading: WakeCategory, trailing: WakeCategory) -> int:
    return WAKE_INTERVAL_S.get((leading, trailing), DEFAULT_WAKE_INTERVAL_S)


class SimView:
    """The read-only slice of engine state the validator needs.

    The engine implements this interface; tests can stub it.
    """

    airport: Airport

    def get_aircraft(self, callsign: str) -> Optional[Aircraft]:
        raise NotImplementedError

    def runway_physically_occupied_by(self, runway_id: str,
                                      exclude: str) -> list[str]:
        """Callsigns whose ground position touches the runway's area."""
        raise NotImplementedError

    def runway_operation_blockers(self, runway_id: str,
                                  exclude: str) -> list[str]:
        """Callsigns with an active rolling/lined-up/cleared operation on this
        runway or any runway sharing pavement with it."""
        raise NotImplementedError

    def arrival_seconds_to_runway(self, runway_id: str,
                                  exclude: str) -> Optional[float]:
        """Seconds until the closest inbound arrival reaches this runway (or
        an intersecting one), None if nobody is inbound."""
        raise NotImplementedError

    def seconds_since_last_runway_op(
            self, runway_id: str) -> Optional[tuple[float, WakeCategory]]:
        """(seconds elapsed, wake category) of the last takeoff/landing."""
        raise NotImplementedError

    def is_runway_closed(self, runway_id: str) -> bool:
        raise NotImplementedError

    def active_runway_ends(self) -> set[str]:
        raise NotImplementedError

    def separation_multiplier(self) -> float:
        return 1.0


TAXIABLE_STATES = {
    AircraftState.TAXI_OUT,
    AircraftState.TAXI_IN,
    AircraftState.HOLD_SHORT,
}

GROUND_MOVING_STATES = TAXIABLE_STATES | {AircraftState.LINE_UP_WAIT}


class Validator:
    def __init__(self, sim: SimView):
        self.sim = sim
        self.airport = sim.airport

    # -- helpers ------------------------------------------------------------

    def _resolve_runway(self, name: str):
        """Accept '05/23', '05' or '23'."""
        rw = self.airport.runways.get(name)
        if rw is None:
            rw = self.airport.runway_for_end(name)
        return rw

    # -- entry point --------------------------------------------------------

    def validate(self, instr: Instruction) -> ValidationResult:
        ac = self.sim.get_aircraft(instr.callsign)
        if ac is None:
            return ValidationResult.reject(
                RejectCode.UNKNOWN_CALLSIGN,
                f"no aircraft {instr.callsign} on frequency",
                Severity.MINOR)
        handler = {
            "approve_pushback": self._validate_pushback,
            "taxi_instruction": self._validate_taxi,
            "runway_clearance": self._validate_runway_clearance,
            "hold_position": self._validate_hold,
            "resume_taxi": self._validate_resume,
            "cross_runway": self._validate_cross,
            "contact_next_frequency": self._validate_contact,
        }[instr.kind]
        return handler(ac, instr)

    # -- per-instruction rules ----------------------------------------------

    def _validate_pushback(self, ac: Aircraft, instr) -> ValidationResult:
        if ac.state != AircraftState.SCHEDULED:
            return ValidationResult.reject(
                RejectCode.BAD_STATE,
                f"{ac.callsign} is {ac.state.value}, not awaiting pushback",
                Severity.MINOR)
        if ac.frequency != Frequency.GROUND:
            return ValidationResult.reject(
                RejectCode.WRONG_FREQUENCY,
                f"{ac.callsign} is not on ground frequency",
                Severity.MINOR)
        return ValidationResult.accept()

    def _validate_taxi(self, ac: Aircraft,
                       instr: TaxiInstruction) -> ValidationResult:
        if ac.state not in TAXIABLE_STATES:
            return ValidationResult.reject(
                RejectCode.BAD_STATE,
                f"{ac.callsign} is {ac.state.value}; cannot accept a taxi "
                "instruction",
                Severity.MINOR)
        if ac.frequency != Frequency.GROUND:
            return ValidationResult.reject(
                RejectCode.WRONG_FREQUENCY,
                f"{ac.callsign} is on {ac.frequency.value}, not ground",
                Severity.MINOR)
        route = list(instr.route)
        if not route:
            return ValidationResult.reject(
                RejectCode.ROUTE_EMPTY, "empty taxi route", Severity.MINOR)
        # Normalize: drop a leading node equal to current position.
        if ac.position.node is not None and route and \
                route[0] == ac.position.node:
            route = route[1:]
            if not route:
                return ValidationResult.reject(
                    RejectCode.ROUTE_EMPTY,
                    "route contains only the current position",
                    Severity.MINOR)
        # Connectivity from present position.
        full = route
        if ac.position.node is not None:
            if self.airport.edge_between(ac.position.node, route[0]) is None:
                return ValidationResult.reject(
                    RejectCode.ROUTE_NOT_FROM_POSITION,
                    f"route does not start from {ac.callsign}'s position "
                    f"({ac.position.node})",
                    Severity.MINOR)
            full = [ac.position.node] + route
        else:
            if route[0] != ac.position.edge_b:
                return ValidationResult.reject(
                    RejectCode.ROUTE_NOT_FROM_POSITION,
                    f"{ac.callsign} is between {ac.position.edge_a} and "
                    f"{ac.position.edge_b}; route must continue via "
                    f"{ac.position.edge_b}",
                    Severity.MINOR)
        for a, b in zip(full, full[1:]):
            edge = self.airport.edge_between(a, b)
            if edge is None:
                return ValidationResult.reject(
                    RejectCode.ROUTE_DISCONNECTED,
                    f"no taxiway connects {a} to {b}",
                    Severity.MINOR)
            if edge.type.value == "runway":
                return ValidationResult.reject(
                    RejectCode.ROUTE_ALONG_RUNWAY,
                    f"segment {a}-{b} taxis along a runway; taxi routes must "
                    "use taxiways (crossings are allowed at marked nodes)",
                    Severity.MINOR)
        if instr.hold_short_at is not None and \
                instr.hold_short_at not in full:
            return ValidationResult.reject(
                RejectCode.HOLD_POINT_NOT_ON_ROUTE,
                f"hold-short point {instr.hold_short_at} is not on the route",
                Severity.MINOR)
        return ValidationResult.accept()

    def _validate_runway_clearance(
            self, ac: Aircraft, instr: RunwayClearance) -> ValidationResult:
        rw = self._resolve_runway(instr.runway)
        if rw is None or instr.runway not in rw.ends:
            return ValidationResult.reject(
                RejectCode.UNKNOWN_RUNWAY,
                f"unknown runway end '{instr.runway}'",
                Severity.MINOR)
        end = instr.runway
        if self.sim.is_runway_closed(rw.id):
            return ValidationResult.reject(
                RejectCode.RUNWAY_CLOSED,
                f"runway {rw.id} is closed",
                Severity.MAJOR)
        if ac.frequency != Frequency.TOWER:
            return ValidationResult.reject(
                RejectCode.WRONG_FREQUENCY,
                f"{ac.callsign} is on {ac.frequency.value}; runway "
                "clearances are issued on tower frequency",
                Severity.MINOR)
        if instr.clearance_type == ClearanceType.LAND:
            return self._validate_land(ac, rw, end)
        if end not in self.sim.active_runway_ends() and not ac.emergency:
            return ValidationResult.reject(
                RejectCode.INACTIVE_RUNWAY,
                f"runway {end} is not in the active configuration",
                Severity.MINOR)
        if instr.clearance_type == ClearanceType.LINE_UP_AND_WAIT:
            return self._validate_luaw(ac, rw, end)
        return self._validate_takeoff(ac, rw, end)

    def _at_hold_point_for(self, ac: Aircraft, rw, end: str) -> bool:
        """True if the aircraft is holding at (or taxiing to) the hold-short
        node adjacent to this runway end's threshold."""
        threshold = rw.threshold(end)
        node = ac.position.node
        candidates: list[str] = []
        if node is not None:
            candidates.append(node)
        # Also accept: still taxiing, with the hold point on the remaining
        # route (clearance in advance, consumed on arrival).
        candidates.extend(ac.clearances.taxi_route)
        for c in candidates:
            n = self.airport.nodes.get(c)
            if n is not None and n.hold_short_for == rw.id and \
                    self.airport.edge_between(c, threshold) is not None:
                return True
        return False

    def _validate_luaw(self, ac: Aircraft, rw, end: str) -> ValidationResult:
        if ac.state not in (AircraftState.HOLD_SHORT, AircraftState.TAXI_OUT):
            return ValidationResult.reject(
                RejectCode.BAD_STATE,
                f"{ac.callsign} is {ac.state.value}; line-up requires an "
                "aircraft holding short",
                Severity.MINOR)
        if not self._at_hold_point_for(ac, rw, end):
            return ValidationResult.reject(
                RejectCode.NOT_AT_RUNWAY_HOLD_POINT,
                f"{ac.callsign} is not at the hold point for runway {end}",
                Severity.MINOR)
        blockers = self.sim.runway_operation_blockers(rw.id, ac.callsign)
        if blockers:
            return ValidationResult.reject(
                RejectCode.RUNWAY_OCCUPIED,
                f"runway {end}: active operation by {', '.join(blockers)}",
                Severity.CRITICAL)
        eta = self.sim.arrival_seconds_to_runway(rw.id, ac.callsign)
        if eta is not None and eta < SHORT_FINAL_CRITICAL_S:
            return ValidationResult.reject(
                RejectCode.ARRIVAL_ON_SHORT_FINAL,
                f"arrival on short final for runway {rw.id} ({eta:.0f}s out)",
                Severity.CRITICAL)
        return ValidationResult.accept()

    def _validate_takeoff(self, ac: Aircraft, rw, end: str) -> ValidationResult:
        if ac.state == AircraftState.LINE_UP_WAIT:
            if ac.clearances.runway_clearance_end != end:
                return ValidationResult.reject(
                    RejectCode.WRONG_APPROACH,
                    f"{ac.callsign} is lined up on runway "
                    f"{ac.clearances.runway_clearance_end}, not {end}",
                    Severity.MINOR)
        elif ac.state in (AircraftState.HOLD_SHORT, AircraftState.TAXI_OUT):
            if not self._at_hold_point_for(ac, rw, end):
                return ValidationResult.reject(
                    RejectCode.NOT_AT_RUNWAY_HOLD_POINT,
                    f"{ac.callsign} is not at the hold point for runway {end}",
                    Severity.MINOR)
        else:
            return ValidationResult.reject(
                RejectCode.BAD_STATE,
                f"{ac.callsign} is {ac.state.value}; cannot accept a takeoff "
                "clearance",
                Severity.MINOR)

        occupants = self.sim.runway_physically_occupied_by(rw.id, ac.callsign)
        if occupants:
            return ValidationResult.reject(
                RejectCode.RUNWAY_OCCUPIED,
                f"runway {end} occupied by {', '.join(occupants)}",
                Severity.CRITICAL)
        blockers = self.sim.runway_operation_blockers(rw.id, ac.callsign)
        if blockers:
            return ValidationResult.reject(
                RejectCode.RUNWAY_OCCUPIED,
                f"runway {end}: active operation by {', '.join(blockers)}",
                Severity.CRITICAL)
        eta = self.sim.arrival_seconds_to_runway(rw.id, ac.callsign)
        if eta is not None and eta < SHORT_FINAL_S:
            sev = (Severity.CRITICAL if eta < SHORT_FINAL_CRITICAL_S
                   else Severity.MAJOR)
            return ValidationResult.reject(
                RejectCode.ARRIVAL_ON_SHORT_FINAL,
                f"arrival {eta:.0f}s from runway {rw.id}",
                sev)
        last = self.sim.seconds_since_last_runway_op(rw.id)
        if last is not None:
            elapsed, leading_wake = last
            required = wake_interval_s(leading_wake, ac.wake) * \
                self.sim.separation_multiplier()
            if elapsed < required:
                return ValidationResult.reject(
                    RejectCode.WAKE_SEPARATION,
                    f"wake separation behind {leading_wake.value}-category "
                    f"traffic: {elapsed:.0f}s of {required:.0f}s elapsed",
                    Severity.MAJOR)
        return ValidationResult.accept()

    def _validate_land(self, ac: Aircraft, rw, end: str) -> ValidationResult:
        if ac.state != AircraftState.ARRIVING:
            return ValidationResult.reject(
                RejectCode.BAD_STATE,
                f"{ac.callsign} is {ac.state.value}, not on approach",
                Severity.MINOR)
        if ac.plan.runway != end:
            return ValidationResult.reject(
                RejectCode.WRONG_APPROACH,
                f"{ac.callsign} is established on the approach to runway "
                f"{ac.plan.runway}, not {end}",
                Severity.MINOR)
        if end not in self.sim.active_runway_ends() and not ac.emergency:
            return ValidationResult.reject(
                RejectCode.INACTIVE_RUNWAY,
                f"runway {end} is not in the active configuration",
                Severity.MINOR)
        return ValidationResult.accept()

    def _validate_hold(self, ac: Aircraft, instr) -> ValidationResult:
        if ac.state not in GROUND_MOVING_STATES:
            return ValidationResult.reject(
                RejectCode.BAD_STATE,
                f"{ac.callsign} is {ac.state.value}; hold position is not "
                "applicable",
                Severity.MINOR)
        return ValidationResult.accept()

    def _validate_resume(self, ac: Aircraft, instr) -> ValidationResult:
        if ac.state not in TAXIABLE_STATES:
            return ValidationResult.reject(
                RejectCode.BAD_STATE,
                f"{ac.callsign} is {ac.state.value}; nothing to resume",
                Severity.MINOR)
        if not ac.clearances.holding_position:
            return ValidationResult.reject(
                RejectCode.NOTHING_TO_RESUME,
                f"{ac.callsign} is not holding position",
                Severity.MINOR)
        return ValidationResult.accept()

    def _validate_cross(self, ac: Aircraft,
                        instr: CrossRunway) -> ValidationResult:
        rw = self._resolve_runway(instr.runway)
        if rw is None:
            return ValidationResult.reject(
                RejectCode.UNKNOWN_RUNWAY,
                f"unknown runway '{instr.runway}'",
                Severity.MINOR)
        if ac.state not in TAXIABLE_STATES:
            return ValidationResult.reject(
                RejectCode.BAD_STATE,
                f"{ac.callsign} is {ac.state.value}; cannot cross",
                Severity.MINOR)
        # The crossing must actually be ahead: current node or a route node
        # must be a hold-short point protecting this runway.
        candidates = ([ac.position.node] if ac.position.node else []) + \
            list(ac.clearances.taxi_route)
        at_crossing = any(
            self.airport.hold_short_runway(c) == rw.id for c in candidates)
        if not at_crossing:
            return ValidationResult.reject(
                RejectCode.NO_CROSSING_AHEAD,
                f"{ac.callsign} has no runway {rw.id} crossing on its route",
                Severity.MINOR)
        blockers = self.sim.runway_operation_blockers(rw.id, ac.callsign)
        if blockers:
            return ValidationResult.reject(
                RejectCode.CROSSING_UNSAFE,
                f"runway {rw.id}: active operation by {', '.join(blockers)}",
                Severity.CRITICAL)
        eta = self.sim.arrival_seconds_to_runway(rw.id, ac.callsign)
        if eta is not None and eta < SHORT_FINAL_CRITICAL_S:
            return ValidationResult.reject(
                RejectCode.CROSSING_UNSAFE,
                f"arrival {eta:.0f}s from runway {rw.id}",
                Severity.CRITICAL)
        return ValidationResult.accept()

    def _validate_contact(self, ac: Aircraft, instr) -> ValidationResult:
        if ac.plan.kind == FlightKind.DEPARTURE and \
                ac.frequency == Frequency.GROUND:
            return ValidationResult.accept()
        if ac.plan.kind == FlightKind.ARRIVAL and \
                ac.frequency == Frequency.TOWER and \
                ac.state in (AircraftState.TAXI_IN, AircraftState.AT_GATE):
            return ValidationResult.accept()
        return ValidationResult.reject(
            RejectCode.NO_NEXT_FREQUENCY,
            f"{ac.callsign} has no next frequency from "
            f"{ac.frequency.value} in state {ac.state.value}",
            Severity.MINOR)
