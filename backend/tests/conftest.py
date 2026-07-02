from __future__ import annotations

import pytest

from atc.airport import Airport
from atc.engine import SimEngine
from atc.models import FlightKind
from atc.scenario import (
    PilotErrorRatesModel,
    Scenario,
    TrafficEntry,
    WeatherState,
)


@pytest.fixture(scope="session")
def airport() -> Airport:
    return Airport.load("kmbs")


def dep(callsign: str, gate: str, runway: str = "05", sched: int = 10,
        type_code: str = "B738") -> TrafficEntry:
    return TrafficEntry(callsign=callsign, type_code=type_code,
                        kind=FlightKind.DEPARTURE, gate=gate, runway=runway,
                        scheduled_time_s=sched)


def arr(callsign: str, gate: str, runway: str = "05", sched: int = 300,
        type_code: str = "E145") -> TrafficEntry:
    return TrafficEntry(callsign=callsign, type_code=type_code,
                        kind=FlightKind.ARRIVAL, gate=gate, runway=runway,
                        scheduled_time_s=sched)


def make_engine(airport: Airport, traffic: list[TrafficEntry],
                **overrides) -> SimEngine:
    kwargs = dict(
        id="test", name="test", seed=7, duration_s=3600,
        scan_interval_s=20, active_ends=["05", "23", "14", "32"],
        weather=WeatherState(),
        pilot_error_rates=PilotErrorRatesModel(),
        traffic=traffic,
    )
    kwargs.update(overrides)
    scenario = Scenario(**kwargs)
    return SimEngine(airport, scenario, controller=None)


def step_until(engine: SimEngine, pred, max_ticks: int = 2000,
               what: str = "condition") -> None:
    for _ in range(max_ticks):
        if pred():
            return
        engine.step()
    raise AssertionError(
        f"{what} not reached within {max_ticks} ticks (t={engine.t})")


# Standard taxi routes on the KMBS graph (from the P-node ramp positions).
ROUTE_P2_TO_HS05 = ["A4", "HS_A_E", "RW14_A", "HS_A_W", "A3", "A2", "A1",
                    "HS_05"]
ROUTE_P3_TO_HS05 = ["P2"] + ROUTE_P2_TO_HS05
ROUTE_P1_TO_HS05 = ["P2"] + ROUTE_P2_TO_HS05
ROUTE_P2_TO_HS23 = ["A4", "A5", "HS_23"]
ROUTE_P1_TO_HS14 = ["TW_G", "HS_14"]
