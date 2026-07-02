"""Scenario definitions: traffic schedule, weather, runway configuration and
injected failures. Scenarios are plain JSON so the benchmark library is data,
not code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .models import AIRCRAFT_TYPES, FlightKind
from .pilot import PilotErrorRates

SCENARIO_DIR = Path(__file__).parent / "scenarios"


class WeatherState(BaseModel):
    wind_dir_deg: float = 50.0
    wind_kt: float = 8.0
    visibility_sm: float = 10.0
    # Multiplier on taxi speed (rain/snow < 1.0).
    taxi_speed_factor: float = 1.0
    # Multiplier on wake separation minima (low visibility > 1.0).
    separation_multiplier: float = 1.0

    def describe(self) -> str:
        return (f"wind {self.wind_dir_deg:03.0f} at {self.wind_kt:.0f} kt, "
                f"visibility {self.visibility_sm:g} SM")


class PilotErrorRatesModel(BaseModel):
    readback_error: float = 0.0
    stuck_mic: float = 0.0
    slow_response: float = 0.0

    def to_rates(self) -> PilotErrorRates:
        return PilotErrorRates(
            readback_error=self.readback_error,
            stuck_mic=self.stuck_mic,
            slow_response=self.slow_response,
        )


class TrafficEntry(BaseModel):
    callsign: str
    type_code: str
    kind: FlightKind
    gate: str
    runway: str
    scheduled_time_s: int  # departures: off-block; arrivals: touchdown
    destination: Optional[str] = None
    origin: Optional[str] = None


class ScenarioEvent(BaseModel):
    """A timed injection into the run."""

    at_s: int
    kind: Literal[
        "weather_change",   # params: WeatherState fields (partial ok)
        "runway_change",    # params: {active_ends: [..], reassign_arrivals: {old_end: new_end}}
        "runway_closure",   # params: {runway_id}
        "runway_reopen",    # params: {runway_id}
        "emergency",        # params: {callsign}
        "go_around",        # params: {callsign}  (forced, e.g. deer on runway)
    ]
    params: dict = Field(default_factory=dict)
    announce: str = ""  # text pushed to the controller as a SYSTEM event


class Scenario(BaseModel):
    id: str
    name: str
    description: str = ""
    airport: str = "kmbs"
    seed: int = 1
    duration_s: int = 3600
    scan_interval_s: int = 20
    active_ends: list[str] = Field(default_factory=lambda: ["05"])
    weather: WeatherState = Field(default_factory=WeatherState)
    pilot_error_rates: PilotErrorRatesModel = Field(
        default_factory=PilotErrorRatesModel)
    traffic: list[TrafficEntry] = Field(default_factory=list)
    events: list[ScenarioEvent] = Field(default_factory=list)
    # Scoring target used by the efficiency score.
    target_throughput_ops_per_hour: float = 12.0

    def validate_traffic(self) -> None:
        for t in self.traffic:
            if t.type_code not in AIRCRAFT_TYPES:
                raise ValueError(
                    f"{t.callsign}: unknown aircraft type {t.type_code}")


def load_scenario(scenario_id: str) -> Scenario:
    path = SCENARIO_DIR / f"{scenario_id}.json"
    with open(path) as f:
        sc = Scenario(**json.load(f))
    sc.validate_traffic()
    return sc


def list_scenarios() -> list[dict]:
    out = []
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        with open(path) as f:
            raw = json.load(f)
        if "id" not in raw:
            continue  # instruction scripts etc. live alongside scenarios
        out.append({
            "id": raw["id"],
            "name": raw.get("name", raw["id"]),
            "description": raw.get("description", ""),
            "duration_s": raw.get("duration_s", 3600),
            "aircraft": len(raw.get("traffic", [])),
        })
    return out
