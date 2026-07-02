"""The LLM controller: serializes sim state into a controller-facing view,
calls the Anthropic API with the instruction tool schema, validates the
returned tool calls through the Phase 1 validation layer, and feeds each
call's accept/reject outcome back as its tool_result.

Design notes:
- One `messages.create` per decision point; the conversation is continuous so
  the model keeps situational memory, with old turns trimmed (the fresh
  state snapshot in every user turn makes trimming safe).
- The system prompt (role + airport graph + procedures) is static and
  carries a cache_control breakpoint, so repeated decisions hit the prompt
  cache. Volatile state lives in user turns only.
- The controller is self-applying: it applies instructions inside decide()
  so the validation verdicts can be returned as tool_result blocks on the
  next call — like a real controller hearing the readback or the alert.
"""

from __future__ import annotations

import json
from typing import Any, Optional, TYPE_CHECKING

from ..airport import Airport
from ..controllers import Controller
from ..instructions import Instruction, parse_instruction
from ..models import Event
from .tools import TOOL_NAMES, TOOLS

if TYPE_CHECKING:
    from ..engine import SimEngine

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_HISTORY_TURNS = 8  # user/assistant pairs kept before trimming
MAX_TOKENS = 2000

WAKE_TABLE_TEXT = (
    "Wake-turbulence minimum intervals behind the previous operation on the "
    "same runway (seconds, multiplied by the weather separation factor): "
    "H->H 90, H->M 120, H->L 180, M->L 120, all other pairs 60."
)

PROCEDURES_TEXT = """\
PROCEDURES
- Frequencies: taxi instructions and pushback approvals require the aircraft
  on GROUND; runway clearances require TOWER. Use contact_next_frequency to
  hand off (departures ground->tower before departure; arrivals tower->ground
  after vacating).
- Departures: approve_pushback -> taxi_instruction to the hold-short node of
  the departure runway -> cross_runway for any runway crossings en route ->
  handoff to tower -> line_up_and_wait / takeoff clearance.
- Arrivals: issue the landing clearance in good time (they go around at the
  threshold without one). After landing they vacate automatically and call
  for taxi; hand them to ground and issue a taxi_instruction to their gate.
- Aircraft stop automatically at every hold-short line. A taxi route that
  crosses a runway needs a cross_runway clearance at the line, or the
  aircraft will sit and wait (RUNWAY_QUEUE delay).
- Only one aircraft may use a runway at a time; intersecting runways
  conflict at shared pavement. The validator rejects unsafe clearances and
  every rejection is logged AGAINST YOUR SCORE, so think before you clear.
- Readbacks matter: pilots occasionally mishear (readback errors), miss a
  call (stuck mic, no readback), or respond slowly. Compare readbacks with
  what you issued and re-issue corrected instructions immediately when they
  do not match.
- Score = safety (incidents, rejected instructions) + delay minutes +
  throughput + decision latency. Safety first, then keep traffic moving.

RESPONSE FORMAT
- Respond with tool calls only (you may batch several per decision point).
- If nothing needs doing, respond with brief text and no tool calls.
"""


def describe_airport(airport: Airport) -> str:
    """Compact, static description of the airport graph for the system
    prompt. Node coordinates are omitted — topology and lengths matter."""
    lines = [f"AIRPORT {airport.id} — {airport.name}", airport.description, ""]
    lines.append("RUNWAYS")
    for rw in airport.runways.values():
        ends = " / ".join(
            f"end {e} threshold={n}" for e, n in rw.ends.items())
        lines.append(f"- {rw.id} ({rw.length_m:.0f} m): {ends}; "
                     f"on-runway nodes in order: {' -> '.join(rw.nodes)}")
    lines.append("")
    lines.append("GATES: " + ", ".join(airport.gates))
    lines.append("")
    lines.append("HOLD-SHORT NODES (node -> protected runway)")
    hs = [f"- {n.id} protects {n.hold_short_for}"
          for n in airport.nodes.values() if n.hold_short_for]
    lines.extend(sorted(hs))
    lines.append("")
    lines.append("TAXIWAY GRAPH (edge: nodeA-nodeB taxiway length)")
    for e in airport.edges.values():
        if e.type.value == "runway":
            continue
        lines.append(f"- {e.a}-{e.b}  {e.name}  {e.length_m:.0f}m")
    lines.append("")
    lines.append(
        "Runway crossings on taxi routes: taxiway A crosses runway 14/32 "
        "via HS_A_W <-> RW14_A <-> HS_A_E; taxiway D crosses runway 05/23 "
        "via HS_D_N <-> RW05_D <-> HS_D_S. Every route between the terminal "
        "(P1/P2/P3) and the runway 05 threshold hold point (HS_05) crosses "
        "runway 14/32 on taxiway A.")
    return "\n".join(lines)


def build_system_prompt(airport: Airport) -> str:
    return (
        "You are the air traffic controller (combined ground + tower) at "
        f"{airport.name}. You control all ground movement and runway "
        "operations through the provided tools. This is a scored benchmark: "
        "you are evaluated on safety incidents, total delay, throughput and "
        "decision latency.\n\n"
        + describe_airport(airport) + "\n\n"
        + WAKE_TABLE_TEXT + "\n\n"
        + PROCEDURES_TEXT
    )


class LLMController(Controller):
    name = "llm"
    self_applying = True

    def __init__(self, model: str = DEFAULT_MODEL,
                 api_key: Optional[str] = None,
                 max_history_turns: int = MAX_HISTORY_TURNS,
                 thinking: Optional[str] = None):
        import anthropic  # deferred so headless/scripted runs need no key

        self.client = anthropic.Anthropic(api_key=api_key) if api_key \
            else anthropic.Anthropic()
        self.model = model
        self.max_history_turns = max_history_turns
        self.thinking = thinking  # None or "adaptive"
        self.messages: list[dict] = []
        self._system: Optional[list[dict]] = None
        # tool_use_id -> outcome text, delivered on the next call.
        self._pending_results: list[tuple[str, str, bool]] = []
        self.call_log: list[dict] = []

    # ------------------------------------------------------------------

    def _ensure_system(self, engine: "SimEngine") -> None:
        if self._system is None:
            self._system = [{
                "type": "text",
                "text": build_system_prompt(engine.airport),
                "cache_control": {"type": "ephemeral"},
            }]

    def _trim_history(self) -> None:
        """Keep the last N user/assistant pairs. If the resulting first user
        message starts with tool_result blocks, their tool_use ids now dangle
        — replace them with a text note."""
        max_msgs = self.max_history_turns * 2
        if len(self.messages) <= max_msgs:
            return
        self.messages = self.messages[-max_msgs:]
        while self.messages and self.messages[0]["role"] != "user":
            self.messages.pop(0)
        if not self.messages:
            return
        first = self.messages[0]
        content = first.get("content")
        if isinstance(content, list):
            kept = [b for b in content
                    if not (isinstance(b, dict)
                            and b.get("type") == "tool_result")]
            if len(kept) != len(content):
                kept.insert(0, {
                    "type": "text",
                    "text": "[earlier exchanges trimmed from context]",
                })
            first["content"] = kept

    def _build_user_message(self, view: dict) -> dict:
        content: list[dict] = []
        for tool_use_id, text, is_error in self._pending_results:
            content.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": text,
                "is_error": is_error,
            })
        self._pending_results = []
        content.append({
            "type": "text",
            "text": "CURRENT STATE\n" + json.dumps(view, indent=1),
        })
        return {"role": "user", "content": content}

    # ------------------------------------------------------------------

    def decide(self, engine: "SimEngine", view: dict,
               new_events: list[Event]) -> list[Instruction]:
        self._ensure_system(engine)
        self.messages.append(self._build_user_message(view))
        self._trim_history()

        kwargs: dict[str, Any] = {}
        if self.thinking == "adaptive":
            kwargs["thinking"] = {"type": "adaptive"}
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=self._system,
                tools=TOOLS,
                messages=self.messages,
                **kwargs,
            )
        except Exception as exc:  # API failure: log, skip this decision
            self.messages.pop()  # keep history consistent
            self.call_log.append({"t": engine.t, "error": str(exc)})
            engine.emit_controller_note(f"[LLM API error: {exc}]")
            return []

        # Echo the assistant turn back into history verbatim.
        assistant_content = [b.model_dump(exclude_none=True)
                             for b in response.content]
        if not assistant_content:
            assistant_content = [{"type": "text", "text": "(no action)"}]
        self.messages.append({"role": "assistant",
                              "content": assistant_content})

        commentary: list[str] = []
        applied: list[Instruction] = []
        n_rejected = 0
        if response.stop_reason == "refusal":
            engine.emit_controller_note("[LLM refused to respond]")
        for block in response.content:
            if block.type == "text" and block.text.strip():
                commentary.append(block.text.strip())
            if block.type != "tool_use":
                continue
            outcome, ok, instr = self._handle_tool_call(
                engine, block.name, block.input)
            self._pending_results.append((block.id, outcome, not ok))
            if ok and instr is not None:
                applied.append(instr)
            elif not ok:
                n_rejected += 1

        usage = response.usage
        self.call_log.append({
            "t": engine.t,
            "stop_reason": response.stop_reason,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_input_tokens":
                getattr(usage, "cache_read_input_tokens", None),
            "instructions": len(applied),
            "rejected": n_rejected,
            "commentary": " ".join(commentary)[:500] if commentary else None,
        })
        if commentary:
            engine.emit_controller_note(" ".join(commentary))
        return applied

    def _handle_tool_call(
            self, engine: "SimEngine", name: str,
            args: dict) -> tuple[str, bool, Optional[Instruction]]:
        """Parse + validate + apply one tool call; return the tool_result
        text, whether it was accepted, and the parsed instruction."""
        if name not in TOOL_NAMES:
            return (f"UNKNOWN TOOL '{name}'", False, None)
        try:
            data = dict(args)
            data["kind"] = name
            instr = parse_instruction(data)
        except Exception as exc:
            return (f"MALFORMED ARGUMENTS: {exc}", False, None)
        result = engine.apply_instruction(instr)
        if result.ok:
            return ("ACCEPTED", True, instr)
        sev = result.severity.value if result.severity else "?"
        return (f"REJECTED ({sev}): {result.reason}", False, instr)
