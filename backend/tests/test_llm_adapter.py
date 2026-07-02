"""LLM adapter tests with a stubbed Anthropic client — verifies tool-call
parsing, validation feedback, history management and failure handling
without network access."""

import json

import pytest

from atc.llm.adapter import LLMController, build_system_prompt
from atc.llm.tools import TOOLS
from atc.models import AircraftState

from conftest import dep, make_engine, step_until


class Block:
    def __init__(self, **kw):
        self.type = kw.get("type")
        self.text = kw.get("text")
        self.name = kw.get("name")
        self.input = kw.get("input")
        self.id = kw.get("id")

    def model_dump(self, exclude_none=True):
        d = {"type": self.type}
        if self.type == "text":
            d["text"] = self.text
        else:
            d.update({"name": self.name, "input": self.input, "id": self.id})
        return d


class Usage:
    input_tokens = 1000
    output_tokens = 50
    cache_read_input_tokens = 800


class Response:
    def __init__(self, content, stop_reason="tool_use"):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = Usage()


class StubMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if not self.responses:
            return Response([Block(type="text", text="(no action)")],
                            stop_reason="end_turn")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class StubClient:
    def __init__(self, responses):
        self.messages = StubMessages(responses)


def make_llm(responses) -> LLMController:
    ctrl = LLMController.__new__(LLMController)
    ctrl.client = StubClient(responses)
    ctrl.model = "stub"
    ctrl.max_history_turns = 3
    ctrl.thinking = None
    ctrl.messages = []
    ctrl._system = None
    ctrl._pending_results = []
    ctrl.call_log = []
    return ctrl


def test_system_prompt_contains_airport(airport):
    prompt = build_system_prompt(airport)
    assert "05/23" in prompt and "14/32" in prompt
    assert "HS_A_E" in prompt
    assert "cross_runway" in prompt


def test_tool_schema_matches_instruction_kinds():
    names = {t["name"] for t in TOOLS}
    assert names == {
        "approve_pushback", "taxi_instruction", "runway_clearance",
        "hold_position", "resume_taxi", "cross_runway",
        "contact_next_frequency",
    }


def test_decide_applies_valid_and_rejects_invalid(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    step_until(engine, lambda: "UAL1" in engine.aircraft and
               engine.aircraft["UAL1"].pending_request == "pushback",
               what="spawn")
    ctrl = make_llm([Response([
        Block(type="text", text="Approving pushback."),
        Block(type="tool_use", id="tu_1", name="approve_pushback",
              input={"callsign": "UAL1"}),
        Block(type="tool_use", id="tu_2", name="runway_clearance",
              input={"callsign": "UAL1", "runway": "05",
                     "clearance_type": "takeoff"}),
        Block(type="tool_use", id="tu_3", name="bogus_tool",
              input={}),
    ])])
    engine.controller = ctrl

    applied = ctrl.decide(engine, engine.controller_view(), [])
    assert len(applied) == 1
    assert engine.aircraft["UAL1"].state == AircraftState.PUSHBACK

    results = {r[0]: (r[1], r[2]) for r in ctrl._pending_results}
    assert results["tu_1"][0] == "ACCEPTED" and results["tu_1"][1] is False
    assert results["tu_2"][0].startswith("REJECTED") and results["tu_2"][1]
    assert "UNKNOWN TOOL" in results["tu_3"][0]

    # Next call: tool_result blocks precede the state text, ids match.
    ctrl.client.messages.responses = []
    ctrl.decide(engine, engine.controller_view(), [])
    user_msgs = [m for m in ctrl.messages if m["role"] == "user"]
    user_msg = user_msgs[-1]
    kinds = [b["type"] for b in user_msg["content"]]
    assert kinds[:3] == ["tool_result"] * 3 and kinds[-1] == "text"
    assert {b["tool_use_id"] for b in user_msg["content"]
            if b["type"] == "tool_result"} == {"tu_1", "tu_2", "tu_3"}


def test_history_trimming_never_leaves_dangling_tool_results(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    step_until(engine, lambda: "UAL1" in engine.aircraft, what="spawn")
    responses = [
        Response([Block(type="tool_use", id=f"tu_{i}", name="hold_position",
                        input={"callsign": "UAL1"})])
        for i in range(10)
    ]
    ctrl = make_llm(responses)
    engine.controller = ctrl
    for _ in range(10):
        ctrl.decide(engine, engine.controller_view(), [])
        engine.step()
    assert len(ctrl.messages) <= ctrl.max_history_turns * 2 + 2
    assert ctrl.messages[0]["role"] == "user"
    # Every tool_result in history must reference a tool_use in the
    # immediately preceding assistant message.
    for i, msg in enumerate(ctrl.messages):
        if msg["role"] != "user" or not isinstance(msg["content"], list):
            continue
        result_ids = {b["tool_use_id"] for b in msg["content"]
                      if isinstance(b, dict) and b["type"] == "tool_result"}
        if not result_ids:
            continue
        assert i > 0, "dangling tool_result at start of history"
        prev = ctrl.messages[i - 1]
        use_ids = {b["id"] for b in prev["content"]
                   if isinstance(b, dict) and b.get("type") == "tool_use"}
        assert result_ids <= use_ids

    # The whole history must be JSON-serializable (what the SDK requires).
    json.dumps(ctrl.messages)


def test_api_error_skips_decision_and_keeps_history_consistent(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    step_until(engine, lambda: "UAL1" in engine.aircraft, what="spawn")
    ctrl = make_llm([RuntimeError("boom"),
                     Response([Block(type="text", text="ok")],
                              stop_reason="end_turn")])
    engine.controller = ctrl
    out = ctrl.decide(engine, engine.controller_view(), [])
    assert out == []
    assert ctrl.messages == []  # failed turn rolled back
    assert "error" in ctrl.call_log[0]
    out2 = ctrl.decide(engine, engine.controller_view(), [])
    assert out2 == []
    assert [m["role"] for m in ctrl.messages] == ["user", "assistant"]


def test_engine_does_not_double_apply_for_self_applying(airport):
    engine = make_engine(airport, [dep("UAL1", "G3", sched=10)])
    step_until(engine, lambda: "UAL1" in engine.aircraft and
               engine.aircraft["UAL1"].pending_request == "pushback",
               what="spawn")
    ctrl = make_llm([Response([
        Block(type="tool_use", id="tu_1", name="approve_pushback",
              input={"callsign": "UAL1"}),
    ])])
    engine.controller = ctrl
    engine._call_controller()
    # Exactly one ATC instruction event (a second apply would add a
    # rejection event since the state already changed).
    atc_events = [e for e in engine.events
                  if e.type.value == "atc_instruction"]
    rejections = [e for e in engine.events
                  if e.type.value == "instruction_rejected"]
    assert len(atc_events) == 1
    assert not rejections
