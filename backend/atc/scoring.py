"""Metrics, the composite controller-efficiency score, and run reports.

score = w1*(1 - normalized_delay)
      + w2*(1 - normalized_incidents)
      + w3*(throughput / target_throughput)
      + w4*(1 - normalized_latency)
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from .delays import aircraft_delay_summary, is_on_time
from .models import EventType, FlightKind, IncidentType, Severity

if TYPE_CHECKING:
    from .engine import SimEngine

WEIGHTS = {"delay": 0.35, "incidents": 0.35, "throughput": 0.20,
           "latency": 0.10}

# Normalization anchors.
DELAY_NORM_S_PER_AIRCRAFT = 600.0     # 10 min of delay per aircraft -> 1.0
LATENCY_NORM_MS = 30_000.0            # p95 of 30 s -> 1.0
SEVERITY_WEIGHT = {Severity.MINOR: 0.1, Severity.MAJOR: 0.45,
                   Severity.CRITICAL: 1.0}
INCIDENT_NORM_PER_AIRCRAFT = 1.0      # 1.0 weighted incident/aircraft -> 1.0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct / 100.0 * (len(ordered) - 1))))
    return ordered[idx]


def incident_counts(engine: "SimEngine") -> dict:
    """Counts by severity, split into sim incidents vs rejected instructions
    (both are controller errors, but they read differently in a report)."""
    incidents = {"minor": 0, "major": 0, "critical": 0}
    rejections = {"minor": 0, "major": 0, "critical": 0}
    by_type: dict[str, int] = {}
    for ev in engine.events:
        if ev.incident is None or ev.severity is None:
            continue
        bucket = (rejections if ev.incident ==
                  IncidentType.REJECTED_INSTRUCTION else incidents)
        bucket[ev.severity.value] += 1
        by_type[ev.incident.value] = by_type.get(ev.incident.value, 0) + 1
    return {"incidents": incidents, "rejections": rejections,
            "by_type": by_type}


def compute_metrics(engine: "SimEngine") -> dict:
    aircraft = [engine.aircraft[cs] for cs in engine.order]
    n = max(1, len(aircraft))
    hours = max(engine.t, 1) / 3600.0

    total_delay_s = sum(ac.total_delay_s for ac in aircraft)
    delay_by_cause: dict[str, float] = {}
    for ac in aircraft:
        for cause, secs in ac.delay_by_cause.items():
            delay_by_cause[cause] = delay_by_cause.get(cause, 0.0) + secs

    on_time_flags = [is_on_time(ac, engine.t) for ac in aircraft]
    decided = [f for f in on_time_flags if f is not None]
    otp = (sum(1 for f in decided if f) / len(decided)) if decided else None

    taxi_ratios = []
    for ac in aircraft:
        if ac.min_taxi_time_s and ac.taxi_ended_s is not None and \
                ac.min_taxi_time_s > 0:
            taxi_ratios.append(ac.actual_taxi_time_s / ac.min_taxi_time_s)
    avg_taxi_ratio = (sum(taxi_ratios) / len(taxi_ratios)
                      if taxi_ratios else None)

    counts = incident_counts(engine)
    weighted_incidents = sum(
        SEVERITY_WEIGHT[Severity(sev)] * cnt
        for sev, cnt in counts["incidents"].items()) + sum(
        SEVERITY_WEIGHT[Severity(sev)] * cnt * 0.5
        for sev, cnt in counts["rejections"].items())

    throughput = (engine.completed_takeoffs + engine.completed_landings) / \
        hours
    latencies = [d["latency_ms"] for d in engine.decision_log]
    avg_latency = (sum(latencies) / len(latencies)) if latencies else None
    p95_latency = _percentile(latencies, 95)

    norm_delay = _clamp01(total_delay_s / (n * DELAY_NORM_S_PER_AIRCRAFT))
    norm_incidents = _clamp01(weighted_incidents /
                              (n * INCIDENT_NORM_PER_AIRCRAFT))
    norm_latency = _clamp01((p95_latency or 0.0) / LATENCY_NORM_MS)
    target = engine.scenario.target_throughput_ops_per_hour
    throughput_score = _clamp01(throughput / target) if target > 0 else 1.0

    score = (
        WEIGHTS["delay"] * (1.0 - norm_delay)
        + WEIGHTS["incidents"] * (1.0 - norm_incidents)
        + WEIGHTS["throughput"] * throughput_score
        + WEIGHTS["latency"] * (1.0 - norm_latency)
    )

    return {
        "t_s": engine.t,
        "aircraft_total": len(aircraft),
        "completed_takeoffs": engine.completed_takeoffs,
        "completed_landings": engine.completed_landings,
        "total_delay_min": round(total_delay_s / 60.0, 2),
        "delay_by_cause_min": {k: round(v / 60.0, 2)
                               for k, v in sorted(delay_by_cause.items())},
        "on_time_performance": None if otp is None else round(otp, 3),
        "avg_taxi_ratio": None if avg_taxi_ratio is None
        else round(avg_taxi_ratio, 2),
        "throughput_ops_per_hour": round(throughput, 2),
        "incidents": counts["incidents"],
        "rejections": counts["rejections"],
        "incidents_by_type": counts["by_type"],
        "weighted_incidents": round(weighted_incidents, 2),
        "go_arounds": sum(ac.go_arounds for ac in aircraft),
        "llm_latency_avg_ms": None if avg_latency is None
        else round(avg_latency, 1),
        "llm_latency_p95_ms": None if p95_latency is None
        else round(p95_latency, 1),
        "decision_points": len(engine.decision_log),
        "efficiency_score": round(score, 4),
        "score_components": {
            "normalized_delay": round(norm_delay, 3),
            "normalized_incidents": round(norm_incidents, 3),
            "throughput_score": round(throughput_score, 3),
            "normalized_latency": round(norm_latency, 3),
            "weights": WEIGHTS,
        },
    }


def delay_report(engine: "SimEngine") -> dict:
    """The Phase 1 deliverable: per-aircraft scheduled-vs-actual breakdown
    plus fleet metrics."""
    return {
        "scenario": engine.scenario.id,
        "seed": engine.scenario.seed,
        "metrics": compute_metrics(engine),
        "aircraft": [aircraft_delay_summary(engine.aircraft[cs])
                     for cs in engine.order],
    }


def incident_log(engine: "SimEngine") -> list[dict]:
    return [
        {"t_s": ev.t_s, "type": ev.incident.value,
         "severity": ev.severity.value if ev.severity else None,
         "callsign": ev.callsign, "text": ev.text, "data": ev.data}
        for ev in engine.events if ev.incident is not None
    ]


def format_delay_report(report: dict) -> str:
    """Human-readable text rendering of a delay report."""
    m = report["metrics"]
    lines = [
        f"=== Delay report: scenario '{report['scenario']}' "
        f"(seed {report['seed']}) ===",
        f"sim time: {m['t_s']}s   aircraft: {m['aircraft_total']}   "
        f"takeoffs: {m['completed_takeoffs']}   "
        f"landings: {m['completed_landings']}",
        f"total delay: {m['total_delay_min']:.1f} min   "
        f"OTP: {m['on_time_performance']}   "
        f"throughput: {m['throughput_ops_per_hour']}/h   "
        f"avg taxi ratio: {m['avg_taxi_ratio']}",
        f"incidents: {m['incidents']}   rejections: {m['rejections']}",
        f"efficiency score: {m['efficiency_score']}",
        "",
        "delay by cause (min): " + ", ".join(
            f"{k}={v}" for k, v in m["delay_by_cause_min"].items()),
        "",
    ]
    for ac in report["aircraft"]:
        head = (f"  {ac['callsign']:<8} {ac['kind']:<9} "
                f"state={ac['state']:<12} delay={ac['total_delay_s']:>7.1f}s")
        if ac["taxi_ratio"]:
            head += f"  taxi x{ac['taxi_ratio']}"
        if ac["go_arounds"]:
            head += f"  go-arounds={ac['go_arounds']}"
        lines.append(head)
        if ac["delay_by_cause"]:
            lines.append("           " + ", ".join(
                f"{k}={v}s" for k, v in ac["delay_by_cause"].items()))
    return "\n".join(lines)
