"""Delay tracking and attribution.

Every second an aircraft is not making progress it *could* be making gets
attributed to a cause. The mapping from "why is this aircraft stopped" to a
DelayCause is the policy encoded here:

- GATE_HOLD          waiting at the gate/ramp for ATC action
- TAXI_CONGESTION    blocked by other traffic (or waiting for a gate/route)
- RUNWAY_QUEUE       waiting at a hold-short line, lined up, or airborne
                     holding for a runway (incl. go-around laps)
- ATC_INSTRUCTION_ERROR  waiting because the controller's instruction was
                     rejected, an unnecessary hold, or a fault go-around
- WEATHER            extra taxi time from reduced taxi speed

`StopReason` is what the movement code observed; `attribute()` turns it into
a scoreable cause given context.
"""

from __future__ import annotations

import enum
from typing import Optional

from .models import Aircraft, AircraftState, DelayCause, NodeType

# A rejection within this window makes subsequent waiting the controller's
# fault rather than generic congestion.
REJECTION_FAULT_WINDOW_S = 90
# An instructed hold with no traffic within this radius is an unnecessary
# (controller-error) hold.
JUSTIFIED_HOLD_RADIUS_M = 250.0


class StopReason(str, enum.Enum):
    NO_INSTRUCTION = "NO_INSTRUCTION"      # nothing to do, waiting on ATC
    BLOCKED_TRAFFIC = "BLOCKED_TRAFFIC"    # leader gap / node occupied / deadlock
    ATC_HOLD = "ATC_HOLD"                  # "hold position" in effect
    ATC_HOLD_SHORT = "ATC_HOLD_SHORT"      # instructed hold-short point
    HOLD_SHORT_WAIT = "HOLD_SHORT_WAIT"    # at a hold-short line, no clearance
    LINE_UP_WAIT = "LINE_UP_WAIT"          # on the runway awaiting takeoff
    GATE_WAIT = "GATE_WAIT"                # SCHEDULED past off-block time
    AIRBORNE_QUEUE = "AIRBORNE_QUEUE"      # ARRIVING past scheduled touchdown


def attribute(
    ac: Aircraft,
    reason: StopReason,
    t_s: int,
    at_node_type: Optional[NodeType],
    traffic_nearby: bool,
) -> DelayCause:
    """Map an observed stop to a delay cause."""
    recently_rejected = (
        ac.last_rejection_s is not None
        and t_s - ac.last_rejection_s <= REJECTION_FAULT_WINDOW_S
    )
    if reason == StopReason.GATE_WAIT:
        return DelayCause.GATE_HOLD
    if reason == StopReason.NO_INSTRUCTION:
        if recently_rejected:
            return DelayCause.ATC_INSTRUCTION_ERROR
        if at_node_type in (NodeType.GATE, NodeType.RAMP):
            return DelayCause.GATE_HOLD
        return DelayCause.TAXI_CONGESTION
    if reason == StopReason.BLOCKED_TRAFFIC:
        return DelayCause.TAXI_CONGESTION
    if reason == StopReason.ATC_HOLD:
        return (DelayCause.TAXI_CONGESTION if traffic_nearby
                else DelayCause.ATC_INSTRUCTION_ERROR)
    if reason in (StopReason.ATC_HOLD_SHORT, StopReason.HOLD_SHORT_WAIT,
                  StopReason.LINE_UP_WAIT):
        return DelayCause.RUNWAY_QUEUE
    if reason == StopReason.AIRBORNE_QUEUE:
        return (DelayCause.ATC_INSTRUCTION_ERROR if ac.atc_fault_go_around
                else DelayCause.RUNWAY_QUEUE)
    return DelayCause.TAXI_CONGESTION


# On-time threshold, industry-standard "D15/A15".
ON_TIME_WINDOW_S = 15 * 60


def aircraft_delay_summary(ac: Aircraft) -> dict:
    """Per-aircraft delay breakdown for reports and the UI detail panel."""
    sched = ac.plan.scheduled_time_s
    if ac.plan.kind.value == "departure":
        actual = ac.actual_offblock_s
        headline = None if actual is None else actual - sched
    else:
        actual = ac.actual_ongate_s
        headline = None if actual is None else actual - sched - (
            ac.min_taxi_time_s or 0)
    taxi_ratio = None
    if ac.min_taxi_time_s and ac.actual_taxi_time_s:
        taxi_ratio = ac.actual_taxi_time_s / ac.min_taxi_time_s
    return {
        "callsign": ac.callsign,
        "kind": ac.plan.kind.value,
        "scheduled_s": sched,
        "state": ac.state.value,
        "headline_delay_s": headline,
        "delay_by_cause": {k: round(v, 1) for k, v in
                           ac.delay_by_cause.items()},
        "total_delay_s": round(ac.total_delay_s, 1),
        "min_taxi_time_s": (round(ac.min_taxi_time_s, 1)
                            if ac.min_taxi_time_s else None),
        "actual_taxi_time_s": round(ac.actual_taxi_time_s, 1),
        "taxi_ratio": round(taxi_ratio, 2) if taxi_ratio else None,
        "go_arounds": ac.go_arounds,
        "transitions": [
            {"state": tr.state.value, "scheduled_s": tr.scheduled_s,
             "actual_s": tr.actual_s}
            for tr in ac.transitions
        ],
    }


def is_on_time(ac: Aircraft, now_s: int) -> Optional[bool]:
    """True/False once determinable, None while still pending."""
    sched = ac.plan.scheduled_time_s
    if ac.plan.kind.value == "departure":
        if ac.actual_offblock_s is not None:
            return ac.actual_offblock_s - sched <= ON_TIME_WINDOW_S
        return False if now_s - sched > ON_TIME_WINDOW_S else None
    # Arrival: on time if on gate within window of scheduled touchdown plus
    # its own minimum taxi-in time.
    budget = sched + (ac.min_taxi_time_s or 300) + ON_TIME_WINDOW_S
    if ac.actual_ongate_s is not None:
        return ac.actual_ongate_s <= budget
    if ac.state == AircraftState.AT_GATE:
        return True
    return False if now_s > budget else None
