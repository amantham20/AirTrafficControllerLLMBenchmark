"""Controller interface: the sim calls `decide()` at every decision point
(pending pilot request, conflict alert, rejection feedback, or periodic scan)
and applies the returned instructions through the validation layer.

`ScriptedController` replays a fixed time-indexed instruction script — the
deterministic harness used by tests and the Phase 1 deliverable. The LLM
controller lives in `atc.llm.adapter`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .instructions import Instruction, parse_instruction
from .models import Event

if TYPE_CHECKING:
    from .engine import SimEngine


class Controller:
    """Base interface. Implementations must be synchronous — the engine tick
    blocks on the decision, which is what makes runs reproducible."""

    name = "base"

    def decide(self, engine: "SimEngine", view: dict,
               new_events: list[Event]) -> list[Instruction]:
        raise NotImplementedError


class NullController(Controller):
    """Issues nothing. Aircraft sit and delays accrue — the floor baseline."""

    name = "null"

    def decide(self, engine, view, new_events):
        return []


class ScriptedController(Controller):
    """Replays `(at_s, instruction)` pairs: every entry whose time has come
    is issued, in order. Instructions may be Instruction models or dicts
    (parsed via the same discriminated union the LLM adapter uses)."""

    name = "scripted"

    def __init__(self, script: list[tuple[int, Instruction | dict]]):
        entries: list[tuple[int, Instruction]] = []
        for at_s, instr in script:
            if isinstance(instr, dict):
                instr = parse_instruction(instr)
            entries.append((at_s, instr))
        self.entries = sorted(entries, key=lambda e: e[0])
        self._cursor = 0

    def decide(self, engine, view, new_events):
        out: list[Instruction] = []
        while self._cursor < len(self.entries) and \
                self.entries[self._cursor][0] <= engine.t:
            out.append(self.entries[self._cursor][1])
            self._cursor += 1
        return out
