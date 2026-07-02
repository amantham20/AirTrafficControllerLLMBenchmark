# ATC Benchmark — LLM-Controlled Air Traffic Simulation

A benchmark harness where an LLM plays air traffic controller (combined
ground + tower) at a simulated airport and is scored on **safety**,
**efficiency**, and **delay minimization**. The backend owns a deterministic,
tick-based simulation with physically consistent state, enforced separation
rules, and full logging of every LLM decision; the frontend renders the live
airport picture, the comms transcript, and a metrics dashboard.

![architecture](https://img.shields.io/badge/backend-FastAPI%20%2B%20WebSocket-blue)
![frontend](https://img.shields.io/badge/frontend-React%20%2B%20SVG-61dafb)
![llm](https://img.shields.io/badge/LLM-Anthropic%20tool--use-d97757)

## How it works

```
sim engine (1 tick = 1 sim second, seeded, deterministic)
   │  state diff ──────────────► WebSocket ──► React UI (map / transcript / metrics)
   │
   ├─ decision point?  (pilot request · conflict alert · rejection · 15–30s scan)
   │        │
   │        ▼
   │   LLM adapter ──► Anthropic API (tool-use schema, no free-text parsing)
   │        │
   │        ▼
   │   validation layer (same conflict logic as the sim)
   │        ├─ valid   → applied + readback (pilots can mishear!)
   │        └─ invalid → rejected + logged as a controller error (scoring signal)
   ▼
 SQLite run store (events, snapshots, metrics) → scorecards, comparisons, replay
```

- **Airport**: a KMBS (MBS International) — inspired layout, simplified from the
  FAA diagram: two intersecting runways (05/23, 14/32), taxiway A crossing
  runway 14/32 with hold-short nodes, taxiway D crossing runway 05/23, and a
  six-gate terminal. Every taxi route from the terminal to runway 05 requires a
  runway crossing clearance — that's where controllers earn their pay.
- **Pilots** are deterministic rule-based agents (the benchmark variable is the
  controller LLM), with configurable readback-error / stuck-mic / slow-response
  rates. A misheard "hold short" becomes a runway incursion unless the
  controller catches the bad readback and re-issues.
- **Separation** is enforced: single-occupancy runways (intersections count),
  wake-turbulence intervals, hold-short compliance, taxiway node/edge conflict
  and deadlock detection.
- **Delays** are attributed every second to `GATE_HOLD`, `TAXI_CONGESTION`,
  `RUNWAY_QUEUE`, `ATC_INSTRUCTION_ERROR`, or `WEATHER`, and actual taxi time
  is compared against the theoretical minimum (the routing-efficiency signal).
- **Score**: `w1·(1−delay) + w2·(1−incidents) + w3·throughput/target + w4·(1−latency)`
  with tunable weights in `backend/atc/scoring.py`.

## Quick start

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                          # 68 tests, all offline
```

### Headless scripted run (no API key)

```bash
python -m atc.run_scripted --scenario scripted_demo
```

### Headless LLM run (Phase 2 deliverable)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -m atc.run_llm --scenario baseline --model claude-sonnet-4-6
# → delay report + incident log + runs/baseline_claude-sonnet-4-6.json
```

### Live UI

```bash
cd frontend && npm install && npm run build && cd ..
cd backend && uvicorn atc.server:app --port 8000
# open http://localhost:8000 — pick a scenario, press start
```

For frontend development: `npm run dev` in `frontend/` proxies to `:8000`.

### Benchmark suite

```bash
python -m atc.benchmark suite --model claude-sonnet-4-6      # all 5 scenarios
python -m atc.benchmark run --scenario rush_hour --model claude-sonnet-4-6 --seed 7
python -m atc.benchmark compare --scenario rush_hour          # same-seed model comparison
python -m atc.benchmark scorecard                             # aggregate per label
```

Every run's full event log and snapshots land in `runs.sqlite3`; any stored
run can be replayed through the UI (replay picker in the header).

## Scenarios

| id | tests | complications |
|----|-------|---------------|
| `baseline` | basic competence | light traffic, good weather |
| `rush_hour` | queueing & throughput | 14 movements/hour, constant 14/32 crossing pressure |
| `runway_change` | adaptability | mid-run wind shift forces all ops from 05 to 23 |
| `degraded` | graceful degradation | 05/23 closed by a disabled aircraft, 12% readback errors, stuck mics, rain |
| `emergency` | prioritization | B763 heavy declares an engine fire on approach |

Scenarios are plain JSON in `backend/atc/scenarios/` — traffic schedule,
weather, active runways, pilot error rates, and timed injections
(`runway_closure`, `runway_change`, `weather_change`, `emergency`,
`go_around`). The UI also has live inject buttons for manual stress-testing.

## Controller tools (the LLM's interface)

`approve_pushback` · `taxi_instruction(callsign, route, hold_short_at)` ·
`runway_clearance(callsign, runway, takeoff|land|line_up_and_wait)` ·
`cross_runway` · `hold_position` · `resume_taxi` · `contact_next_frequency`

Tool calls are validated against the same conflict logic the sim runs on;
rejections come back as `tool_result` errors *and* as radio events, with a
severity that feeds the safety score. Accepted instructions are rendered as
ATC phraseology for the transcript ("UAL245, runway 05, cleared for takeoff").

## Repository layout

```
backend/
  atc/
    airport.py        # graph, routing, runway geometry
    models.py         # aircraft, flight plans, events, enums
    engine.py         # tick loop, movement, runway ops, conflict detection
    instructions.py   # instruction schema + validation layer
    pilot.py          # rule-based pilots + comm-failure injection
    delays.py         # delay attribution policy
    scoring.py        # metrics, composite score, reports
    scenario.py       # scenario schema/loader   scenarios/*.json
    controllers.py    # scripted/null controllers
    llm/              # Anthropic tool-use adapter (tools.py, adapter.py)
    storage.py        # SQLite run store (events, snapshots, metrics)
    server.py         # FastAPI + WebSocket live sessions & replay
    benchmark.py      # suite runner, comparisons, scorecards
    run_scripted.py   # Phase 1 deliverable CLI
    run_llm.py        # Phase 2 deliverable CLI
  tests/              # 68 pytest tests (conflict detection has priority)
frontend/             # Vite + React + TS: map, transcript, metrics, playback
```

## Determinism

Same scenario + same seed + same controller decisions ⇒ identical run,
byte-for-byte event log (tested). All randomness (pilot comm failures) flows
from one seeded RNG; LLM latency is measured but never affects sim state.
