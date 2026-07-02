"""Deterministic rule-based pilot agent.

Pilots generate requests, read back instructions, and — at configurable,
seeded rates — produce communication failures that stress the controller:

- readback error: the pilot mishears and acts on a mutated instruction; the
  readback transcript reveals the mutation so an attentive controller can
  catch and correct it before it becomes an incursion.
- stuck mic: the instruction never reaches the aircraft (no readback).
- slow response: the instruction is applied after a delay.

Everything uses the engine's seeded RNG so runs stay reproducible.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .airport import Airport
from .instructions import (
    CrossRunway,
    Instruction,
    RunwayClearance,
    TaxiInstruction,
)
from .models import Aircraft, ClearanceType


@dataclass
class PilotErrorRates:
    readback_error: float = 0.0
    stuck_mic: float = 0.0
    slow_response: float = 0.0


@dataclass
class CommOutcome:
    """How the pilot handled an accepted instruction."""

    kind: str  # "normal" | "readback_error" | "stuck_mic" | "slow_response"
    applied_instruction: Optional[Instruction]  # what the aircraft acts on
    delay_s: int = 0
    note: str = ""


@dataclass
class PilotAgent:
    airport: Airport
    rng: random.Random
    rates: PilotErrorRates = field(default_factory=PilotErrorRates)

    def communicate(self, ac: Aircraft, instr: Instruction) -> CommOutcome:
        """Decide how the pilot receives a validated instruction."""
        roll = self.rng.random()
        if roll < self.rates.stuck_mic:
            return CommOutcome(kind="stuck_mic", applied_instruction=None,
                               note="no readback received")
        roll -= self.rates.stuck_mic
        if roll < self.rates.slow_response:
            delay = self.rng.randint(6, 15)
            return CommOutcome(kind="slow_response",
                               applied_instruction=instr, delay_s=delay,
                               note=f"responded after {delay}s")
        roll -= self.rates.slow_response
        if roll < self.rates.readback_error:
            mutated = self._mutate(ac, instr)
            if mutated is not None:
                return CommOutcome(kind="readback_error",
                                   applied_instruction=mutated,
                                   note="readback does not match clearance")
        return CommOutcome(kind="normal", applied_instruction=instr)

    def _mutate(self, ac: Aircraft,
                instr: Instruction) -> Optional[Instruction]:
        """Produce a plausible mishearing. Returns None if this instruction
        has no realistic failure mode (then comms succeed normally)."""
        if isinstance(instr, TaxiInstruction) and instr.hold_short_at:
            # Classic killer: "hold short of runway X" heard as "cross
            # runway X". The aircraft will taxi through the hold point.
            return TaxiInstruction(
                callsign=instr.callsign, route=list(instr.route),
                hold_short_at=None)
        if isinstance(instr, RunwayClearance):
            if instr.clearance_type == ClearanceType.LINE_UP_AND_WAIT:
                # "Line up and wait" heard as "cleared for takeoff".
                return RunwayClearance(
                    callsign=instr.callsign, runway=instr.runway,
                    clearance_type=ClearanceType.TAKEOFF)
            if instr.clearance_type == ClearanceType.TAKEOFF:
                # Takeoff clearance heard as line up and wait: pure delay.
                return RunwayClearance(
                    callsign=instr.callsign, runway=instr.runway,
                    clearance_type=ClearanceType.LINE_UP_AND_WAIT)
        if isinstance(instr, TaxiInstruction) and len(instr.route) >= 3:
            # Wrong turn: truncate the route partway; the aircraft stops
            # somewhere unexpected and congestion follows.
            cut = self.rng.randint(1, len(instr.route) - 2)
            return TaxiInstruction(callsign=instr.callsign,
                                   route=list(instr.route[:cut + 1]),
                                   hold_short_at=None)
        if isinstance(instr, CrossRunway):
            return None  # crossing clearances are short; treat as understood
        return None

    # ------------------------------------------------------------------
    # Request generation (rendered by the engine as PILOT_REQUEST events)
    # ------------------------------------------------------------------

    @staticmethod
    def request_text(ac: Aircraft, request: str) -> str:
        cs = ac.callsign
        gate = ac.plan.gate
        if request == "pushback":
            return (f"{cs} at gate {gate}, ready for pushback, "
                    f"with information Alpha.")
        if request == "taxi":
            return f"{cs}, ready to taxi, runway {ac.plan.runway}."
        if request == "takeoff":
            return f"{cs}, holding short runway {ac.plan.runway}, ready for departure."
        if request == "landing":
            dist_nm = (ac.position.air_dist_m or 0) / 1852.0
            call = "MAYDAY, MAYDAY, MAYDAY, " if ac.emergency else ""
            return (f"{call}{cs}, {dist_nm:.0f} mile final, runway "
                    f"{ac.plan.runway}." if not ac.emergency else
                    f"{call}{cs}, {dist_nm:.0f} mile final runway "
                    f"{ac.plan.runway}, declaring emergency, request priority.")
        if request == "taxi_in":
            return f"{cs}, clear of the runway, taxi to gate {gate}."
        return f"{cs}, {request}."

    @staticmethod
    def unable_text(ac: Aircraft, reason: str) -> str:
        return f"Unable, {ac.callsign}. {reason}"
