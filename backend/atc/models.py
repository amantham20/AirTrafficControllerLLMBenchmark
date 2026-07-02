"""Core data models for the ATC simulation.

Units used throughout the engine: meters, seconds, meters/second.
Performance profiles accept knots for readability and convert on load.
"""

from __future__ import annotations

import enum
from typing import Optional

from pydantic import BaseModel, Field

KT_TO_MS = 0.514444


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NodeType(str, enum.Enum):
    GATE = "gate"
    RAMP = "ramp"
    TAXI = "taxi"
    HOLD_SHORT = "hold_short"
    RUNWAY = "runway"


class EdgeType(str, enum.Enum):
    RAMP = "ramp"
    TAXIWAY = "taxiway"
    RUNWAY = "runway"


class WakeCategory(str, enum.Enum):
    LIGHT = "L"
    MEDIUM = "M"
    HEAVY = "H"


class FlightKind(str, enum.Enum):
    DEPARTURE = "departure"
    ARRIVAL = "arrival"


class AircraftState(str, enum.Enum):
    # Departure path
    SCHEDULED = "SCHEDULED"
    PUSHBACK = "PUSHBACK"
    TAXI_OUT = "TAXI_OUT"
    HOLD_SHORT = "HOLD_SHORT"
    LINE_UP_WAIT = "LINE_UP_WAIT"
    TAKEOFF_ROLL = "TAKEOFF_ROLL"
    DEPARTED = "DEPARTED"
    # Arrival path
    ARRIVING = "ARRIVING"
    LANDING_ROLL = "LANDING_ROLL"
    TAXI_IN = "TAXI_IN"
    AT_GATE = "AT_GATE"


class Frequency(str, enum.Enum):
    GROUND = "GROUND"
    TOWER = "TOWER"
    DEPARTURE = "DEPARTURE"  # handed off / no longer this facility's problem


class ClearanceType(str, enum.Enum):
    TAKEOFF = "takeoff"
    LAND = "land"
    LINE_UP_AND_WAIT = "line_up_and_wait"


class DelayCause(str, enum.Enum):
    GATE_HOLD = "GATE_HOLD"
    TAXI_CONGESTION = "TAXI_CONGESTION"
    RUNWAY_QUEUE = "RUNWAY_QUEUE"
    ATC_INSTRUCTION_ERROR = "ATC_INSTRUCTION_ERROR"
    WEATHER = "WEATHER"


class Severity(str, enum.Enum):
    MINOR = "minor"          # inefficiency / procedural slip
    MAJOR = "major"          # unsafe situation developing (go-around, deadlock)
    CRITICAL = "critical"    # runway incursion, near miss, collision


class IncidentType(str, enum.Enum):
    RUNWAY_INCURSION = "RUNWAY_INCURSION"
    NEAR_MISS = "NEAR_MISS"
    COLLISION = "COLLISION"
    GO_AROUND = "GO_AROUND"
    TAXI_DEADLOCK = "TAXI_DEADLOCK"
    REJECTED_INSTRUCTION = "REJECTED_INSTRUCTION"


class EventType(str, enum.Enum):
    STATE_TRANSITION = "state_transition"
    PILOT_REQUEST = "pilot_request"
    PILOT_READBACK = "pilot_readback"
    PILOT_UNABLE = "pilot_unable"
    ATC_INSTRUCTION = "atc_instruction"
    INSTRUCTION_REJECTED = "instruction_rejected"
    INCIDENT = "incident"
    CONFLICT_ALERT = "conflict_alert"
    SYSTEM = "system"  # weather change, runway closure, scenario injects


# ---------------------------------------------------------------------------
# Performance & flight plan
# ---------------------------------------------------------------------------


class PerformanceProfile(BaseModel):
    """Aircraft performance. Speeds in knots (converted via helpers)."""

    type_code: str
    wake: WakeCategory
    taxi_speed_kt: float = 18.0
    approach_speed_kt: float = 130.0
    rotate_speed_kt: float = 140.0
    takeoff_accel_ms2: float = 2.2
    landing_decel_ms2: float = 2.5
    pushback_duration_s: int = 90
    turnaround_s: int = 1800

    @property
    def taxi_speed_ms(self) -> float:
        return self.taxi_speed_kt * KT_TO_MS

    @property
    def approach_speed_ms(self) -> float:
        return self.approach_speed_kt * KT_TO_MS

    @property
    def rotate_speed_ms(self) -> float:
        return self.rotate_speed_kt * KT_TO_MS

    @property
    def takeoff_roll_m(self) -> float:
        """Distance to reach rotate speed at constant acceleration."""
        v = self.rotate_speed_ms
        return v * v / (2.0 * self.takeoff_accel_ms2)


# A small library of realistic profiles keyed by ICAO type code.
AIRCRAFT_TYPES: dict[str, PerformanceProfile] = {
    "B738": PerformanceProfile(
        type_code="B738", wake=WakeCategory.MEDIUM, taxi_speed_kt=18,
        approach_speed_kt=140, rotate_speed_kt=145),
    "A320": PerformanceProfile(
        type_code="A320", wake=WakeCategory.MEDIUM, taxi_speed_kt=18,
        approach_speed_kt=136, rotate_speed_kt=140),
    "E145": PerformanceProfile(
        type_code="E145", wake=WakeCategory.MEDIUM, taxi_speed_kt=17,
        approach_speed_kt=126, rotate_speed_kt=130),
    "CRJ2": PerformanceProfile(
        type_code="CRJ2", wake=WakeCategory.MEDIUM, taxi_speed_kt=17,
        approach_speed_kt=125, rotate_speed_kt=128),
    "DH8D": PerformanceProfile(
        type_code="DH8D", wake=WakeCategory.MEDIUM, taxi_speed_kt=16,
        approach_speed_kt=115, rotate_speed_kt=110, takeoff_accel_ms2=2.0),
    "C172": PerformanceProfile(
        type_code="C172", wake=WakeCategory.LIGHT, taxi_speed_kt=12,
        approach_speed_kt=65, rotate_speed_kt=55, takeoff_accel_ms2=1.4,
        landing_decel_ms2=1.8),
    "B763": PerformanceProfile(
        type_code="B763", wake=WakeCategory.HEAVY, taxi_speed_kt=18,
        approach_speed_kt=145, rotate_speed_kt=155, takeoff_accel_ms2=2.0),
}


class FlightPlan(BaseModel):
    callsign: str
    type_code: str
    kind: FlightKind
    gate: str
    runway: str  # runway end, e.g. "05"
    # Departures: scheduled off-block time. Arrivals: scheduled touchdown time.
    scheduled_time_s: int
    origin: Optional[str] = None
    destination: Optional[str] = None


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


class Position(BaseModel):
    """Ground position: at a node, or on an edge some distance from `node_a`.

    Airborne aircraft (ARRIVING / DEPARTED) use `air_dist_m`: distance to
    (positive, ARRIVING) or from (DEPARTED) the runway threshold.
    """

    node: Optional[str] = None
    edge_a: Optional[str] = None
    edge_b: Optional[str] = None
    dist_m: float = 0.0
    air_dist_m: Optional[float] = None

    @classmethod
    def at_node(cls, node_id: str) -> "Position":
        return cls(node=node_id)

    @classmethod
    def on_edge(cls, a: str, b: str, dist_m: float) -> "Position":
        return cls(edge_a=a, edge_b=b, dist_m=dist_m)

    @property
    def is_airborne(self) -> bool:
        return self.air_dist_m is not None


# ---------------------------------------------------------------------------
# Aircraft (mutable sim state)
# ---------------------------------------------------------------------------


class Clearances(BaseModel):
    """Active clearances held by an aircraft. The engine only ever moves an
    aircraft onto protected surfaces if the matching clearance is present."""

    pushback_approved: bool = False
    taxi_route: list[str] = Field(default_factory=list)  # remaining node ids
    hold_short_at: Optional[str] = None  # ATC-imposed extra hold point
    holding_position: bool = False       # "hold position" in effect
    cross_runways: set[str] = Field(default_factory=set)   # runway ids, e.g. "14/32"
    runway_clearance: Optional[ClearanceType] = None
    runway_clearance_end: Optional[str] = None  # runway end, e.g. "05"


class DelayRecord(BaseModel):
    """Scheduled vs actual timestamps for one state transition."""

    state: AircraftState
    scheduled_s: Optional[int] = None
    actual_s: int


class Aircraft(BaseModel):
    callsign: str
    profile: PerformanceProfile
    plan: FlightPlan
    state: AircraftState
    position: Position
    heading_deg: float = 0.0
    speed_ms: float = 0.0
    frequency: Frequency = Frequency.GROUND
    clearances: Clearances = Field(default_factory=Clearances)

    # Bookkeeping
    spawned_at_s: Optional[int] = None
    state_since_s: int = 0
    transitions: list[DelayRecord] = Field(default_factory=list)
    delay_by_cause: dict[str, float] = Field(default_factory=dict)
    pending_request: Optional[str] = None  # what the pilot is waiting on
    last_rejection_s: Optional[int] = None  # for ATC error delay attribution
    go_arounds: int = 0
    atc_fault_go_around: bool = False  # last go-around was controller-caused
    emergency: bool = False
    # Actual timeline marks
    actual_offblock_s: Optional[int] = None
    actual_takeoff_s: Optional[int] = None
    actual_touchdown_s: Optional[int] = None
    actual_ongate_s: Optional[int] = None
    # Efficiency: theoretical minimum unimpeded taxi time, set at first taxi
    min_taxi_time_s: Optional[float] = None
    actual_taxi_time_s: float = 0.0
    taxi_started_s: Optional[int] = None
    taxi_ended_s: Optional[int] = None

    def add_delay(self, cause: DelayCause, seconds: float) -> None:
        self.delay_by_cause[cause.value] = (
            self.delay_by_cause.get(cause.value, 0.0) + seconds
        )

    @property
    def total_delay_s(self) -> float:
        return sum(self.delay_by_cause.values())

    @property
    def wake(self) -> WakeCategory:
        return self.profile.wake


# ---------------------------------------------------------------------------
# Events / incidents
# ---------------------------------------------------------------------------


class Event(BaseModel):
    """One entry in the run log. Everything scoreable flows through here."""

    seq: int = 0
    t_s: int
    type: EventType
    callsign: Optional[str] = None
    text: str = ""                 # rendered phraseology / human description
    data: dict = Field(default_factory=dict)
    severity: Optional[Severity] = None
    incident: Optional[IncidentType] = None
