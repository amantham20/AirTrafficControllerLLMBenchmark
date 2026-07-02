"""Render structured instructions and pilot transmissions as ATC phraseology.

The sim applies the structured form; these strings exist for the transcript
panel and run logs, so realism here never affects correctness.
"""

from __future__ import annotations

from .airport import Airport
from .instructions import (
    ApprovePushback,
    ContactNextFrequency,
    CrossRunway,
    HoldPosition,
    Instruction,
    ResumeTaxi,
    RunwayClearance,
    TaxiInstruction,
)
from .models import Aircraft, ClearanceType


def _spell_runway(end_or_id: str) -> str:
    return end_or_id.replace("/", " ")


def route_as_taxiways(airport: Airport, route: list[str]) -> str:
    """Collapse a node route into the taxiway names it follows."""
    names: list[str] = []
    for a, b in zip(route, route[1:]):
        e = airport.edge_between(a, b)
        if e is None:
            continue
        if not names or names[-1] != e.name:
            names.append(e.name)
    shown = [n for n in names if n not in ("ramp", "apron")]
    return ", ".join(shown) if shown else "the ramp"


def render_instruction(airport: Airport, ac: Aircraft,
                       instr: Instruction) -> str:
    cs = instr.callsign
    if isinstance(instr, ApprovePushback):
        return f"{cs}, pushback approved, expect runway {ac.plan.runway}."
    if isinstance(instr, TaxiInstruction):
        full = instr.route
        if ac.position.node is not None and full and \
                full[0] != ac.position.node:
            full = [ac.position.node] + list(full)
        via = route_as_taxiways(airport, full)
        dest = instr.route[-1] if instr.route else "?"
        dest_node = airport.nodes.get(dest)
        if dest_node is not None and dest_node.hold_short_for is not None:
            base = f"{cs}, taxi to runway {ac.plan.runway} via {via}"
        elif dest_node is not None and dest_node.type.value == "gate":
            base = f"{cs}, taxi to gate {dest} via {via}"
        else:
            base = f"{cs}, taxi to {dest} via {via}"
        if instr.hold_short_at:
            rw = airport.hold_short_runway(instr.hold_short_at)
            if rw:
                return f"{base}, hold short of runway {_spell_runway(rw)}."
            return f"{base}, hold short of {instr.hold_short_at}."
        return f"{base}."
    if isinstance(instr, RunwayClearance):
        rw = _spell_runway(instr.runway)
        if instr.clearance_type == ClearanceType.TAKEOFF:
            return f"{cs}, runway {rw}, cleared for takeoff."
        if instr.clearance_type == ClearanceType.LAND:
            return f"{cs}, runway {rw}, cleared to land."
        return f"{cs}, runway {rw}, line up and wait."
    if isinstance(instr, HoldPosition):
        return f"{cs}, hold position."
    if isinstance(instr, ResumeTaxi):
        return f"{cs}, continue taxi."
    if isinstance(instr, CrossRunway):
        return f"{cs}, cross runway {_spell_runway(instr.runway)}."
    if isinstance(instr, ContactNextFrequency):
        return f"{cs}, contact {'tower' if ac.frequency.value == 'GROUND' else 'ground'}."
    return f"{cs}, {instr.kind}."


def render_readback(airport: Airport, ac: Aircraft,
                    instr: Instruction) -> str:
    cs = instr.callsign
    if isinstance(instr, ApprovePushback):
        return f"Pushback approved, {cs}."
    if isinstance(instr, TaxiInstruction):
        full = instr.route
        if ac.position.node is not None and full and \
                full[0] != ac.position.node:
            full = [ac.position.node] + list(full)
        via = route_as_taxiways(airport, full)
        if instr.hold_short_at:
            rw = airport.hold_short_runway(instr.hold_short_at)
            hs = f"runway {_spell_runway(rw)}" if rw else instr.hold_short_at
            return f"Taxi via {via}, hold short of {hs}, {cs}."
        return f"Taxi via {via}, {cs}."
    if isinstance(instr, RunwayClearance):
        rw = _spell_runway(instr.runway)
        if instr.clearance_type == ClearanceType.TAKEOFF:
            return f"Cleared for takeoff runway {rw}, {cs}."
        if instr.clearance_type == ClearanceType.LAND:
            return f"Cleared to land runway {rw}, {cs}."
        return f"Line up and wait runway {rw}, {cs}."
    if isinstance(instr, HoldPosition):
        return f"Holding position, {cs}."
    if isinstance(instr, ResumeTaxi):
        return f"Continuing taxi, {cs}."
    if isinstance(instr, CrossRunway):
        return f"Cross runway {_spell_runway(instr.runway)}, {cs}."
    if isinstance(instr, ContactNextFrequency):
        return f"Over to {'tower' if ac.frequency.value == 'GROUND' else 'ground'}, {cs}."
    return f"Wilco, {cs}."
