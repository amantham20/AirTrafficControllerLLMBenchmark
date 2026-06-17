# ATC // Radar Benchmark

An **Air Traffic Control radar simulator** that doubles as a **model-agnostic AI
benchmark**, built with React + Vite. It has four views:

- **📋 INFO** — scenario briefing: weather/ATIS, objectives, and the traffic table.
- **▶ RADAR** — the approach/departure radar game. Sequence arrivals, launch
  departures, handle emergencies and minimum-fuel aircraft, and keep everyone
  separated while a live score grades your work.
- **🛬 GROUND** — a live **airport surface diagram** generated for each field:
  runways at their true headings with centreline/threshold markings, taxiways,
  a terminal concourse with numbered gates, and aircraft **parked at gates,
  taxiing, holding short, lining up, taking off, and taxiing in after landing**.
  Aircraft on short final/initial climb also appear over the field.
- **⚡ BENCHMARK** — generate a structured prompt for any LLM, paste the model's
  response back in, and get a static score across Safety, Efficiency,
  Phraseology, and Priority (100-point rubric).

Dark phosphor-green radar terminal aesthetic, all-SVG displays, monospace
throughout — no canvas, no external chart libraries.

## Quick start

```bash
npm install
npm run dev      # start the dev server (http://localhost:5173)
```

Other scripts:

```bash
npm run build    # production build into dist/
npm run preview  # serve the production build locally
```

Requires Node 18+ (developed and verified on Node 22).

## Scenarios

| # | Airport | Scenario | Difficulty |
|---|---------|----------|------------|
| 1 | KORD | O'Hare arrival rush — sequence 6 arrivals, depart 2 heavies | Medium |
| 2 | KJFK | Engine-failure emergency — clear BAW175, hold the rest | Hard |
| 3 | KLAX | Simultaneous parallel ILS 24L/24R | Hard |
| 4 | KDEN | Thunderstorm deviation around CB cells, minimum-fuel traffic | Expert |
| 5 | EGLL | Heathrow CAT III fog — single-runway precision sequencing | Expert |

## How to play

Select a scenario, open the **▶ RADAR** tab, and press **START**. Click an
aircraft (on the radar or in the traffic table) to pre-fill its callsign, then
type an instruction and press **TX** / Enter:

```
UAL1234 descend and maintain 3000
AAL567  turn left 210
SWA890  reduce speed 180
DAL234  cleared ILS 10L
AAL100  cleared for takeoff 10R
UAL999  hold
```

Supported commands: `DESCEND/CLIMB [alt]`, `TURN LEFT/RIGHT [hdg]` /
`FLY HEADING [hdg]`, `SPEED [kt]` / `REDUCE SPEED [kt]`, `CLEARED ILS [rwy]`,
`CLEARED TAKEOFF [rwy]`, `HOLD`, `GO AROUND`.

Scoring is live: **Safety** (separation), **Efficiency** (sequencing / fuel /
go-arounds), **Phraseology** (commands issued), and **Priority** (emergency /
minimum-fuel handling), with a letter grade.

The **🛬 GROUND** tab shares the same simulation: clear a departure for takeoff
and watch it line up and roll; vector and clear an arrival and watch it land,
roll out, and taxi to a gate. Resident aircraft sit on the ramp and taxi for
ambience. The whole surface layout (runways, taxiways, terminal, gates) is
generated from each scenario's runway list, so crossing and parallel fields
both look right.

## Benchmark mode

The **⚡ BENCHMARK** tab emits a fully structured prompt (weather, traffic,
objectives, output format, scoring rubric) you can hand to any LLM. Paste the
model's reply into the evaluator to get a per-aircraft breakdown, coverage
stats, missing callsigns, and a graded 100-point score — letting you compare
how different models handle the same ATC situation.

## Project structure

```
index.html              # app shell + radar-themed loading splash
vite.config.js          # Vite + @vitejs/plugin-react
public/radar.svg        # favicon
src/
  main.jsx              # React entry point
  index.css             # global resets + terminal styling
  atc_benchmark.jsx     # scenarios, radar sim, command parser, scoring, benchmark
  airport.jsx           # airport surface layout generator + ground-ops + diagram
```

Most of the app lives in `src/atc_benchmark.jsx` — scenario data, the command
parser, the radar simulation loop, scoring, and the static benchmark evaluator.
`src/airport.jsx` builds each airport's surface diagram from its runway list and
drives ground movement (gate → taxi → hold short → takeoff, and landing →
roll-out → taxi → gate).
