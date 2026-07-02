"""Anthropic tool definitions for the controller — these mirror
`atc.instructions` one-to-one so tool calls parse directly into validated
instruction models with zero free-text parsing."""

from __future__ import annotations

TOOLS: list[dict] = [
    {
        "name": "approve_pushback",
        "description": (
            "Approve pushback for a departure waiting at its gate. The "
            "aircraft pushes back onto the apron taxilane (~90s) and will "
            "then call ready to taxi."),
        "input_schema": {
            "type": "object",
            "properties": {
                "callsign": {"type": "string"},
            },
            "required": ["callsign"],
            "additionalProperties": False,
        },
    },
    {
        "name": "taxi_instruction",
        "description": (
            "Issue a taxi route as an ordered list of node IDs from the "
            "aircraft's present position (do not include the current node). "
            "Consecutive nodes must be connected by a taxiway/ramp edge — "
            "never route along a runway. Routes may cross runways at marked "
            "crossing nodes; the aircraft will automatically stop at every "
            "hold-short line until you issue cross_runway or a runway "
            "clearance. Use hold_short_at to impose an additional hold at a "
            "specific node on the route."),
        "input_schema": {
            "type": "object",
            "properties": {
                "callsign": {"type": "string"},
                "route": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered node IDs, e.g. "
                    "[\"A4\", \"HS_A_E\", \"RW14_A\", \"HS_A_W\", \"A3\"]",
                },
                "hold_short_at": {
                    "type": ["string", "null"],
                    "description": "Optional node on the route where the "
                    "aircraft must hold until further clearance.",
                },
            },
            "required": ["callsign", "route"],
            "additionalProperties": False,
        },
    },
    {
        "name": "runway_clearance",
        "description": (
            "Issue a runway clearance (tower frequency only). "
            "'takeoff': aircraft must be holding short of (or lined up on) "
            "that runway end, runway clear, wake separation satisfied. "
            "'line_up_and_wait': position the aircraft on the runway without "
            "takeoff clearance. "
            "'land': clear an arrival on approach; the runway must be clear "
            "by the time it crosses the threshold or it will go around."),
        "input_schema": {
            "type": "object",
            "properties": {
                "callsign": {"type": "string"},
                "runway": {
                    "type": "string",
                    "description": "Runway END, e.g. \"05\" or \"23\".",
                },
                "clearance_type": {
                    "type": "string",
                    "enum": ["takeoff", "land", "line_up_and_wait"],
                },
            },
            "required": ["callsign", "runway", "clearance_type"],
            "additionalProperties": False,
        },
    },
    {
        "name": "hold_position",
        "description": "Order a taxiing aircraft to stop immediately and "
        "hold position until told to continue.",
        "input_schema": {
            "type": "object",
            "properties": {
                "callsign": {"type": "string"},
            },
            "required": ["callsign"],
            "additionalProperties": False,
        },
    },
    {
        "name": "resume_taxi",
        "description": "Release a hold: the aircraft continues its "
        "previously issued taxi route.",
        "input_schema": {
            "type": "object",
            "properties": {
                "callsign": {"type": "string"},
            },
            "required": ["callsign"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cross_runway",
        "description": (
            "Clear an aircraft to cross a runway at the hold-short point on "
            "its taxi route. Reject-safe: never issued while an aircraft is "
            "rolling on, or short final for, that runway."),
        "input_schema": {
            "type": "object",
            "properties": {
                "callsign": {"type": "string"},
                "runway": {
                    "type": "string",
                    "description": "Runway id (\"14/32\") or either end "
                    "(\"14\").",
                },
            },
            "required": ["callsign", "runway"],
            "additionalProperties": False,
        },
    },
    {
        "name": "contact_next_frequency",
        "description": (
            "Hand the aircraft to its next frequency. Departures: ground -> "
            "tower (required before any runway clearance). Arrivals that "
            "have vacated the runway: tower -> ground (required before "
            "taxi-in instructions)."),
        "input_schema": {
            "type": "object",
            "properties": {
                "callsign": {"type": "string"},
            },
            "required": ["callsign"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}
