import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { buildLayout, buildUnits, stepGround, AirportDiagram, runwayHeading } from './airport.jsx';

/* ============================================================
   ATC SIMULATOR & MODEL-AGNOSTIC AI BENCHMARK
   Dark radar terminal aesthetic. Radar approach view, live
   airport surface (ground) view, and a model-agnostic benchmark.
   ============================================================ */

/* ----------------------------- constants ----------------------------- */
const PPM = 13;                 // pixels per nautical mile
const CX = 240, CY = 240;       // radar center (airport)
const SIM = 28;                 // simulation speed multiplier
const TICK = 80;                // ms per tick
const MOVE = (SIM * 0.08 / 3600) * PPM; // svg px per knot per tick
const ALT_RATE = 15;            // ft per tick toward target

const C = {
  bg: '#070c12', panel: '#0c1520', border: '#152535', radarBg: '#030808',
  ring: '#0a2018', green: '#00e5a0', text: '#8fb8a8', dim: '#3a6a58',
  red: '#ff3355', amber: '#f59e0b', blue: '#4ab8f4', dep: '#fbbf24',
  airport: '#1a5535',
};
const SCORE = { safety: '#10b981', efficiency: '#3b82f6', phraseology: '#a855f7', priority: '#f59e0b' };
const AC_COLORS = ['#5bc8f5', '#f5a623', '#a855f7', '#10b981', '#06b6d4', '#84cc16', '#fb923c', '#f43f5e'];
const FONT = "'Courier New', monospace";

/* ----------------------------- scenarios ----------------------------- */
const SCENARIOS = [
  {
    id: 'kord',
    name: "KORD O'Hare Arrival Rush",
    airport: 'KORD',
    description: 'Sequence 6 northeast arrivals for ILS 10L/10C while departing two heavy jets from 10R. Keep three miles between everyone.',
    wx: 'KORD 251852Z 10012KT 10SM FEW045 18/12 A2992',
    atis: 'Wind 100°/12kt · 10SM · Few 4500 · Alt 29.92 · ILS 10L/10C · DEP 10R',
    runways: ['10L', '10C', '10R'],
    sep: 3,
    difficulty: 'Medium',
    timeLimitSecs: 300,
    objectives: [
      'Sequence the six northeast arrivals onto ILS 10L and 10C.',
      'Maintain 3NM separation — no conflicts.',
      'Depart both heavy jets from runway 10R.',
    ],
    aircraft: [
      { cs: 'UAL1234', type: 'B737', nx: 14, ny: 12, altitude: 5000, speed: 210, heading: 235, status: 'arr', assignedRunway: '10L', fuelMins: 45 },
      { cs: 'AAL567', type: 'A320', nx: 11, ny: 16, altitude: 4000, speed: 195, heading: 214, status: 'arr', assignedRunway: '10C', fuelMins: 38 },
      { cs: 'SWA890', type: 'B737', nx: 17, ny: 8, altitude: 7000, speed: 220, heading: 244, status: 'arr', assignedRunway: '10L', fuelMins: 52 },
      { cs: 'DAL234', type: 'B757', nx: 9, ny: 6, altitude: 3000, speed: 180, heading: 237, status: 'arr', assignedRunway: '10L', fuelMins: 30 },
      { cs: 'FFT567', type: 'B737', nx: 16, ny: -5, altitude: 8000, speed: 230, heading: 288, status: 'arr', assignedRunway: '10C', fuelMins: 60 },
      { cs: 'UAL999', type: 'B737', nx: 12, ny: 14, altitude: 3500, speed: 190, heading: 220, status: 'arr', assignedRunway: '10C', fuelMins: 35 },
      { cs: 'AAL100', type: 'B737', nx: 0, ny: -1, altitude: 0, speed: 0, heading: 100, status: 'dep', assignedRunway: '10R', fuelMins: 999, onGround: true },
      { cs: 'DAL200', type: 'B757', nx: 0, ny: -2, altitude: 0, speed: 0, heading: 100, status: 'dep', assignedRunway: '10R', fuelMins: 999, onGround: true },
    ],
  },
  {
    id: 'kjfk',
    name: 'KJFK Engine Failure Emergency',
    airport: 'KJFK',
    description: 'BAW175 has declared an engine failure inbound. Clear it for an immediate approach to 22L and hold the rest of the arrivals clear.',
    wx: 'KJFK 251900Z 22008KT 10SM BKN035 21/14 A2998',
    atis: 'Wind 220°/8kt · 10SM · Broken 3500 · Alt 29.98 · ILS 22L · DEP 31L',
    runways: ['22L', '31L'],
    sep: 3,
    difficulty: 'Hard',
    timeLimitSecs: 240,
    objectives: [
      'BAW175 has an engine failure — clear it for immediate approach 22L.',
      'Hold all other arrivals clear of the emergency aircraft.',
      'Keep JBU301 on the ground until the emergency is resolved.',
    ],
    aircraft: [
      { cs: 'BAW175', type: 'B77W', nx: 12, ny: 10, altitude: 6000, speed: 200, heading: 230, status: 'arr', assignedRunway: '22L', fuelMins: 20, emergency: true },
      { cs: 'AAL102', type: 'A321', nx: 15, ny: 12, altitude: 8000, speed: 220, heading: 231, status: 'arr', assignedRunway: '22L', fuelMins: 45 },
      { cs: 'DAL901', type: 'B737', nx: 10, ny: 15, altitude: 5000, speed: 200, heading: 214, status: 'arr', assignedRunway: '22L', fuelMins: 38 },
      { cs: 'UAL456', type: 'B738', nx: 18, ny: 8, altitude: 9000, speed: 230, heading: 244, status: 'arr', assignedRunway: '22L', fuelMins: 55 },
      { cs: 'JBU301', type: 'A320', nx: 0, ny: 1, altitude: 0, speed: 0, heading: 310, status: 'dep', assignedRunway: '31L', fuelMins: 999, onGround: true },
    ],
  },
  {
    id: 'klax',
    name: 'KLAX Simultaneous Parallel ILS',
    airport: 'KLAX',
    description: 'Pair six arrivals for simultaneous parallel approaches to 24L and 24R, then push two departures off 25L.',
    wx: 'KLAX 251920Z 24010KT 10SM SKC 24/12 A2998',
    atis: 'Wind 240°/10kt · 10SM · Sky Clear · Alt 29.98 · Simultaneous ILS 24L/24R · DEP 25L',
    runways: ['24L', '24R', '25L'],
    sep: 3,
    difficulty: 'Hard',
    timeLimitSecs: 300,
    objectives: [
      'Pair all six arrivals for simultaneous parallel ILS 24L/24R.',
      'Maintain 3NM separation between the parallel streams.',
      'Depart both aircraft from runway 25L.',
    ],
    aircraft: [
      { cs: 'UAL2', type: 'B789', nx: 14, ny: 10, altitude: 6000, speed: 210, heading: 234, status: 'arr', assignedRunway: '24L', fuelMins: 50 },
      { cs: 'DAL327', type: 'B757', nx: 14, ny: 7, altitude: 6500, speed: 205, heading: 243, status: 'arr', assignedRunway: '24R', fuelMins: 48 },
      { cs: 'SWA44', type: 'B737', nx: 17, ny: 12, altitude: 8000, speed: 220, heading: 235, status: 'arr', assignedRunway: '24L', fuelMins: 55 },
      { cs: 'AAL1', type: 'B77W', nx: 17, ny: 8, altitude: 8500, speed: 225, heading: 244, status: 'arr', assignedRunway: '24R', fuelMins: 60 },
      { cs: 'SKW349', type: 'CRJ7', nx: 10, ny: 14, altitude: 4000, speed: 185, heading: 216, status: 'arr', assignedRunway: '24L', fuelMins: 35 },
      { cs: 'ASA701', type: 'B737', nx: 10, ny: 10, altitude: 4500, speed: 190, heading: 225, status: 'arr', assignedRunway: '24R', fuelMins: 40 },
      { cs: 'WN1204', type: 'B737', nx: 1, ny: 0, altitude: 0, speed: 0, heading: 250, status: 'dep', assignedRunway: '25L', fuelMins: 999, onGround: true },
      { cs: 'UAL540', type: 'A320', nx: -1, ny: 0, altitude: 0, speed: 0, heading: 250, status: 'dep', assignedRunway: '25L', fuelMins: 999, onGround: true },
    ],
  },
  {
    id: 'kden',
    name: 'KDEN Thunderstorm Deviation',
    airport: 'KDEN',
    description: 'A line of CB cells sits NW–SW of the field. Three aircraft are minimum fuel. Vector all five around the weather to ILS 25.',
    wx: 'KDEN 251850Z 27018G28KT 5SM TSRA BKN035CB OVC060 22/15 A2985',
    atis: 'Wind 270°/18G28kt · 5SM TS+RA · BKN 035CB · Alt 29.85 · ILS 25 · CB cells NW–SW',
    runways: ['25', '34R'],
    sep: 5,
    difficulty: 'Expert',
    timeLimitSecs: 360,
    objectives: [
      'Three aircraft are minimum fuel — give them priority.',
      'Vector all five aircraft around the CB cells NW–SW of the field.',
      'Maintain increased 5NM separation for weather ops.',
    ],
    aircraft: [
      { cs: 'UAL372', type: 'A320', nx: 16, ny: 10, altitude: 10000, speed: 240, heading: 238, status: 'arr', assignedRunway: '25', fuelMins: 22, lowFuel: true },
      { cs: 'AAL2040', type: 'B737', nx: 14, ny: 14, altitude: 11000, speed: 250, heading: 225, status: 'arr', assignedRunway: '25', fuelMins: 35 },
      { cs: 'SWA1567', type: 'B737', nx: 18, ny: 6, altitude: 9000, speed: 235, heading: 252, status: 'arr', assignedRunway: '25', fuelMins: 28, lowFuel: true },
      { cs: 'FFT234', type: 'A319', nx: 10, ny: 16, altitude: 8000, speed: 220, heading: 212, status: 'arr', assignedRunway: '25', fuelMins: 42 },
      { cs: 'SKW4021', type: 'CRJ7', nx: 12, ny: 8, altitude: 7000, speed: 210, heading: 236, status: 'arr', assignedRunway: '25', fuelMins: 18, lowFuel: true },
    ],
  },
  {
    id: 'egll',
    name: 'EGLL Heathrow CAT III Fog',
    airport: 'EGLL',
    description: 'CAT III fog with 100m visibility. Sequence six long-haul arrivals onto a single runway 27L with 4NM precision.',
    wx: 'EGLL 251830Z 24004KT 0100 FG VV002 04/04 Q1024',
    atis: 'Wind 240°/4kt · Vis 100m FG · Vert Vis 200ft · QNH 1024 · CAT III ILS 27L · 4nm standard',
    runways: ['27L', '27R'],
    sep: 4,
    difficulty: 'Expert',
    timeLimitSecs: 360,
    objectives: [
      'Sequence all six long-haul arrivals onto single runway 27L.',
      'Maintain 4NM precision separation for CAT III ops.',
      'Manage the single-runway flow in 100m fog.',
    ],
    aircraft: [
      { cs: 'BAW1', type: 'B744', nx: -20, ny: -2, altitude: 5000, speed: 200, heading: 264, status: 'arr', assignedRunway: '27L', fuelMins: 55 },
      { cs: 'VIR25', type: 'A346', nx: -24, ny: 2, altitude: 6000, speed: 205, heading: 275, status: 'arr', assignedRunway: '27L', fuelMins: 65 },
      { cs: 'UAE1', type: 'A388', nx: -28, ny: -3, altitude: 7000, speed: 210, heading: 264, status: 'arr', assignedRunway: '27L', fuelMins: 72 },
      { cs: 'LHR441', type: 'B737', nx: -16, ny: -4, altitude: 4000, speed: 190, heading: 256, status: 'arr', assignedRunway: '27L', fuelMins: 40 },
      { cs: 'AFR1', type: 'A343', nx: -32, ny: 4, altitude: 8000, speed: 215, heading: 277, status: 'arr', assignedRunway: '27L', fuelMins: 80 },
      { cs: 'DLH902', type: 'A320', nx: -14, ny: 2, altitude: 3500, speed: 185, heading: 262, status: 'arr', assignedRunway: '27L', fuelMins: 35 },
    ],
  },
];

/* ----------------------------- pure helpers ----------------------------- */
const pad2 = (n) => String(n).padStart(2, '0');
const pad3 = (n) => {
  const h = ((Math.round(n) % 360) + 360) % 360;
  return String(h === 0 ? 360 : h).padStart(3, '0');
};
function nowStr() {
  const d = new Date();
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}
function formatTime(secs) {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${pad2(m)}:${pad2(s)}`;
}
function spokenAlt(alt) {
  if (alt >= 18000) return `flight level ${alt / 100}`;
  if (alt < 1000) return `${alt}`;
  if (alt % 1000 === 0) return `${alt / 1000} thousand`;
  return `${Math.floor(alt / 1000)} thousand ${alt % 1000}`;
}
function gradeFor(total) {
  if (total >= 97) return 'A+';
  if (total >= 90) return 'A';
  if (total >= 80) return 'B';
  if (total >= 70) return 'C';
  if (total >= 60) return 'D';
  return 'F';
}
function gradeColor(g) {
  if (g === 'A+' || g === 'A') return C.green;
  if (g === 'B') return C.blue;
  if (g === 'C') return C.amber;
  if (g === 'D') return '#fb923c';
  return C.red;
}
function diffColor(d) {
  return d === 'Medium' ? C.blue : d === 'Hard' ? C.amber : C.red;
}
function statusLabel(a) {
  if (a.landed) return 'LAND';
  if (a.departed) return 'DEP↑';
  if (!a.active) return 'LOST';
  if (a.onGround) return 'GND·' + a.assignedRunway;
  if (a.holding) return 'HOLD';
  if (a.cleared) return 'CLRD';
  if (a.emergency) return '⚡EMRG';
  if (a.lowFuel) return '⚠FUEL';
  if (a.status === 'dep') return 'DEPARTING';
  return 'ARRIVING';
}
function statusColor(a) {
  if (a.landed) return C.green;
  if (a.departed) return C.dep;
  if (!a.active) return C.red;
  if (a.onGround) return C.dep;
  if (a.holding) return C.blue;
  if (a.cleared) return C.green;
  if (a.emergency) return C.red;
  if (a.lowFuel) return C.amber;
  return C.text;
}

/* build the runtime aircraft array from scenario data */
function initAircraft(sc) {
  return sc.aircraft.map((a, i) => {
    const svgX = CX + a.nx * PPM;
    const svgY = CY - a.ny * PPM;
    return {
      ...a,
      onGround: !!a.onGround,
      emergency: !!a.emergency,
      lowFuel: !!a.lowFuel,
      svgX, svgY,
      targetAlt: a.altitude,
      cleared: false,
      holding: false,
      landed: false,
      departed: false,
      active: true,
      trail: [{ x: svgX, y: svgY }],
      color: AC_COLORS[i % AC_COLORS.length],
    };
  });
}

/* advance the whole fleet one tick */
function advance(list, sc) {
  return list.map((a) => {
    if (!a.active) return a;
    let { svgX, svgY, altitude, targetAlt, heading, speed, holding, onGround } = a;
    if (altitude < targetAlt) altitude = Math.min(targetAlt, altitude + ALT_RATE);
    else if (altitude > targetAlt) altitude = Math.max(targetAlt, altitude - ALT_RATE);
    let trail = a.trail;
    if (!holding && !onGround) {
      const r = (heading * Math.PI) / 180;
      svgX = svgX + Math.sin(r) * speed * MOVE;
      svgY = svgY - Math.cos(r) * speed * MOVE;
      trail = [...a.trail, { x: svgX, y: svgY }].slice(-6);
    }
    let { active, landed, departed, cleared, status } = a;
    const dist = Math.hypot(svgX - CX, svgY - CY) / PPM;
    if (status === 'arr' && cleared && dist < 2.5) { landed = true; active = false; }
    if (status === 'dep' && !onGround && dist > 17) { departed = true; active = false; }
    if (active && !cleared && (svgX < -30 || svgX > 510 || svgY < -30 || svgY > 510)) active = false;
    return { ...a, svgX, svgY, altitude, trail, active, landed, departed };
  });
}

/* separation violations among active airborne aircraft */
function detectViolations(list, sep) {
  const v = [];
  const air = list.filter((a) => a.active && !a.onGround && !a.landed);
  for (let i = 0; i < air.length; i++) {
    for (let j = i + 1; j < air.length; j++) {
      const a = air[i], b = air[j];
      const horizNm = Math.hypot(a.svgX - b.svgX, a.svgY - b.svgY) / PPM;
      const vertFt = Math.abs(a.altitude - b.altitude);
      if (horizNm < sep && vertFt < 1000) v.push({ pair: [a.cs, b.cs], horizNm, vertFt });
    }
  }
  return v;
}

/* live priority score (game mode) */
function computePriority(list, order) {
  const emerg = list.find((a) => a.emergency);
  if (emerg) {
    if (emerg.landed) return 10;
    if (emerg.cleared) return 6;
    return 2;
  }
  const low = list.filter((a) => a.lowFuel);
  if (low.length > 0) {
    const ok = low.every((a) => a.cleared && order.indexOf(a.cs) > -1 && order.indexOf(a.cs) < 3);
    return ok ? 10 : 6;
  }
  return 10;
}

/* ----------------------------- command parser ----------------------------- */
function parseCommand(raw, list) {
  const upper = raw.trim().toUpperCase();
  if (!upper) return { error: 'EMPTY COMMAND' };
  const sp = upper.indexOf(' ');
  const csToken = sp === -1 ? upper : upper.slice(0, sp);
  const ac = list.find((a) => a.cs.toUpperCase() === csToken);
  if (!ac) return { error: `UNKNOWN CALLSIGN "${csToken}"` };
  const rest = sp === -1 ? '' : upper.slice(sp + 1).trim();
  let m;
  if ((m = rest.match(/CLEARED\s+(?:FOR\s+)?TAKEOFF(?:\s+RUNWAY)?\s+(\d{2}[LRC]?)/)))
    return { cs: ac.cs, type: 'tkof', value: m[1] };
  if ((m = rest.match(/CLEARED\s+(?:ILS|APPROACH)(?:\s+RUNWAY)?\s+(\d{2}[LRC]?)/)))
    return { cs: ac.cs, type: 'ils', value: m[1] };
  if (/GO.?AROUND|MISSED/.test(rest)) return { cs: ac.cs, type: 'ga', value: null };
  if (/\bHOLD\b/.test(rest)) return { cs: ac.cs, type: 'hold', value: null };
  if ((m = rest.match(/(?:DESCEND|CLIMB|MAINTAIN)(?:\s+AND\s+MAINTAIN)?\s+(\d{3,5})/)))
    return { cs: ac.cs, type: 'alt', value: parseInt(m[1], 10) };
  if ((m = rest.match(/(?:TURN\s+(?:LEFT|RIGHT)|FLY\s+HEADING|HEADING)\s+(\d{1,3})/)))
    return { cs: ac.cs, type: 'hdg', value: parseInt(m[1], 10) % 360 };
  if ((m = rest.match(/(?:REDUCE\s+SPEED|SPEED)\s+(\d{2,3})/)))
    return { cs: ac.cs, type: 'spd', value: parseInt(m[1], 10) };
  return { error: `UNRECOGNIZED COMMAND FOR ${ac.cs}` };
}

/* ----------------------------- benchmark prompt ----------------------------- */
function boxHeader(name) {
  const W = 60;
  const top = '╔' + '═'.repeat(W) + '╗';
  const bottom = '╚' + '═'.repeat(W) + '╝';
  const pad = (s) => '║' + ('  ' + s).padEnd(W) + '║';
  return [top, pad('ATC BENCHMARK SCENARIO'), pad(name.toUpperCase()), bottom].join('\n');
}
function divider(title) {
  const full = '━━━ ' + title + ' ';
  return full + '━'.repeat(Math.max(3, 60 - full.length));
}
function buildPrompt(sc) {
  const n = sc.aircraft.length;
  const L = [];
  L.push(boxHeader(sc.name));
  L.push(`AIRPORT  : ${sc.airport}`);
  L.push(`WEATHER  : ${sc.wx}`);
  L.push(`ATIS     : ${sc.atis}`);
  L.push(`RUNWAYS  : ${sc.runways.join(', ')}`);
  L.push(`SEP STD  : ${sc.sep}NM horizontal / 1000ft vertical`);
  L.push('');
  L.push(divider(`TRAFFIC (${n} aircraft)`));
  sc.aircraft.forEach((a) => {
    if (a.onGround) {
      L.push(`  ${a.cs} ${a.type}  Ground · RWY ${a.assignedRunway} · READY`);
    } else {
      let flag = '';
      if (a.emergency) flag = '  ⚠️ MAYDAY — ENGINE FAILURE';
      else if (a.lowFuel) flag = `  ⚠️ MINIMUM FUEL (${a.fuelMins}min)`;
      L.push(`  ${a.cs} ${a.type}  ${a.altitude}ft · ${a.speed}kt · HDG ${a.heading}°${flag}`);
    }
  });
  L.push('');
  L.push(divider('OBJECTIVES'));
  sc.objectives.forEach((o, i) => L.push(`  ${i + 1}. ${o}`));
  L.push('');
  L.push(divider('YOUR TASK'));
  L.push('You are the radar controller. Issue ONE instruction per');
  L.push(`aircraft. Address ALL ${n} aircraft. Use standard ICAO/FAA`);
  L.push('ATC phraseology. Prioritize by urgency.');
  L.push('');
  L.push('OUTPUT FORMAT (one line per aircraft):');
  L.push('  CALLSIGN, FACILITY NAME, instruction.');
  L.push('');
  L.push('EXAMPLES:');
  L.push('  UAL1234, Chicago Approach, descend and maintain three thousand, expect ILS runway one zero left.');
  L.push('  BAW175, Kennedy Approach, turn right heading one eight zero, cleared immediate approach runway two two left.');
  L.push("  AAL100, O'Hare Tower, runway one zero right, cleared for takeoff.");
  L.push('');
  L.push(divider('SCORING RUBRIC (100 points)'));
  L.push('  Safety      40 pts  No conflicts · All aircraft addressed');
  L.push('  Efficiency  30 pts  Optimal sequence · Fuel priority');
  L.push('  Phraseology 20 pts  Correct format · Numbers spoken out');
  L.push('  Priority    10 pts  Emergency / min-fuel aircraft first');
  L.push('═'.repeat(62));
  return L.join('\n');
}

/* ----------------------------- benchmark evaluator ----------------------------- */
function lineStartsWithCS(ul, cs) {
  if (!ul.startsWith(cs)) return false;
  const after = ul[cs.length];
  return after === undefined || !/[A-Z0-9]/.test(after);
}
function evaluateResponse(text, sc) {
  const acList = sc.aircraft;
  const n = acList.length;
  const upperLines = text.split('\n').map((l) => l.trim().toUpperCase()).filter(Boolean);

  const FACILITY = /APPROACH|TOWER|GROUND|CENTER|DEPARTURE|CONTROL|RADAR/;
  const VERB = /DESCEND|CLIMB|MAINTAIN|HEADING|TURN|SPEED|CLEARED|HOLD|VECTOR|EXPEDITE|DIRECT/;
  const NUM = /THOUSAND|HUNDRED|FLIGHT LEVEL|KNOTS/;
  const EMER = /EMERGENCY|IMMEDIATE|PRIORITY|EQUIPMENT/;

  // order in which callsigns first appear
  const order = [];
  upperLines.forEach((ul) => {
    const found = acList.find((a) => lineStartsWithCS(ul, a.cs.toUpperCase()));
    if (found && !order.includes(found.cs)) order.push(found.cs);
  });

  let totalPhrasePoints = 0;
  let maxPossiblePoints = 0;
  const perAC = acList.map((a) => {
    const isEmer = a.emergency;
    const max = isEmer ? 4 : 3;
    maxPossiblePoints += max;
    const idx = upperLines.findIndex((ul) => lineStartsWithCS(ul, a.cs.toUpperCase()));
    if (idx === -1) return { cs: a.cs, addressed: false, points: 0, max, notes: 'NOT ADDRESSED' };
    const ul = upperLines[idx];
    let pts = 0; const notes = [];
    if (FACILITY.test(ul)) pts++; else notes.push('no facility');
    if (VERB.test(ul)) pts++; else notes.push('no action verb');
    if (NUM.test(ul)) pts++; else notes.push('numbers not spoken');
    if (isEmer) { if (EMER.test(ul)) pts++; else notes.push('no emergency phrase'); }
    totalPhrasePoints += pts;
    return { cs: a.cs, addressed: true, points: pts, max, notes: notes.length ? notes.join(', ') : 'good' };
  });

  const addressedCount = perAC.filter((p) => p.addressed).length;
  const missing = perAC.filter((p) => !p.addressed).map((p) => p.cs);
  const missingCount = missing.length;

  // Safety
  const safety = Math.max(0, 40 - missingCount * Math.ceil(40 / n));

  // Efficiency
  let eff = 30;
  const all = upperLines.join('  \n  ');
  if (!/NUMBER \d|FOLLOW|BEHIND|SEQUENCE|IN TRAIL/.test(all) && n > 3) eff -= 8;
  if (!/\d{2,3} KNOTS|SPEED \d|REDUCE SPEED/.test(all) && n > 4) eff -= 5;
  if (!/DESCEND|CLIMB|MAINTAIN \d|FLIGHT LEVEL/.test(all)) eff -= 5;
  eff = Math.max(0, eff);

  // Phraseology
  const phras = maxPossiblePoints > 0
    ? Math.min(20, Math.round((totalPhrasePoints / maxPossiblePoints) * 20)) : 0;

  // Priority
  let priority = 10;
  const emergAC = acList.filter((a) => a.emergency);
  const lowFuelAC = acList.filter((a) => a.lowFuel);
  if (emergAC.length > 0) {
    const emergFirst = emergAC.some((a) => a.cs === order[0]);
    if (!emergFirst) priority -= 4;
    emergAC.forEach((a) => {
      const p = perAC.find((x) => x.cs === a.cs);
      if (!p || !p.addressed) priority -= 6;
    });
  } else if (lowFuelAC.length > 0) {
    const first3 = order.slice(0, 3);
    if (!lowFuelAC.every((a) => first3.includes(a.cs))) priority -= 3;
  }
  priority = Math.max(0, priority);

  const total = Math.round(safety + eff + phras + priority);
  return { safety, eff, phras, priority, total, grade: gradeFor(total), n, addressedCount, missing, perAC };
}

/* ----------------------------- shared styles ----------------------------- */
const panel = { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 6, padding: 12 };
const thS = { padding: '5px 8px', fontWeight: 'normal' };
const tdS = { padding: '4px 8px', color: C.text };
const btnSmall = { background: 'transparent', color: C.green, border: `1px solid ${C.green}`, borderRadius: 4, padding: '5px 11px', fontFamily: FONT, fontSize: 10, cursor: 'pointer' };
const btnPrimary = { background: 'rgba(0,229,160,0.12)', color: C.green, border: `1px solid ${C.green}`, borderRadius: 6, padding: 12, fontFamily: FONT, fontSize: 13, cursor: 'pointer', fontWeight: 'bold' };
const btnAccent = { background: 'rgba(74,184,244,0.12)', color: C.blue, border: `1px solid ${C.blue}`, borderRadius: 6, padding: 12, fontFamily: FONT, fontSize: 13, cursor: 'pointer', fontWeight: 'bold' };
function tabStyle(active) {
  return { background: active ? 'rgba(0,229,160,0.14)' : 'transparent', color: active ? C.green : C.dim, border: `1px solid ${active ? C.green : C.border}`, borderRadius: 4, padding: '6px 11px', fontFamily: FONT, fontSize: 11, cursor: 'pointer', fontWeight: 'bold' };
}
function diffBadge(d) {
  const col = diffColor(d);
  return { color: col, border: `1px solid ${col}`, borderRadius: 4, padding: '2px 9px', fontSize: 10, textTransform: 'uppercase', letterSpacing: 1, whiteSpace: 'nowrap' };
}

/* ----------------------------- main component ----------------------------- */
export default function ATCBenchmark() {
  // refs (mutation without render)
  const acsRef = useRef([]);
  const runRef = useRef(false);
  const safetyRef = useRef(40);
  const phrasRef = useRef(0);
  const goAroundRef = useRef(0);
  const cmdOrderRef = useRef([]);
  const logEndRef = useRef(null);
  const inputRef = useRef(null);
  const groundRef = useRef([]);
  const elapsedRef = useRef(0);

  // state (triggers render)
  const [scIdx, setScIdx] = useState(0);
  const [mode, setMode] = useState('info');
  const [acs, setAcs] = useState(() => initAircraft(SCENARIOS[0]));
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0); // ticks
  const [cmdInput, setCmdInput] = useState('');
  const [log, setLog] = useState([]);
  const [viols, setViols] = useState([]);
  const [safetyPts, setSafetyPts] = useState(40);
  const [phrasPts, setPhrasPts] = useState(0);
  const [selAC, setSelAC] = useState(null);
  const [benchText, setBenchText] = useState('');
  const [benchResult, setBenchResult] = useState(null);
  const [copied, setCopied] = useState(false);
  const [ground, setGround] = useState([]);

  const scenario = SCENARIOS[scIdx];
  const promptText = buildPrompt(scenario);
  const layout = useMemo(() => buildLayout(scenario), [scIdx]); // eslint-disable-line react-hooks/exhaustive-deps

  /* reset / initialize */
  const resetSim = useCallback((idx) => {
    const sc = SCENARIOS[idx];
    const init = initAircraft(sc);
    const lay = buildLayout(sc);
    const units = buildUnits(sc, lay);
    acsRef.current = init;
    groundRef.current = units;
    runRef.current = false;
    safetyRef.current = 40;
    phrasRef.current = 0;
    goAroundRef.current = 0;
    cmdOrderRef.current = [];
    elapsedRef.current = 0;
    setGround(units);
    setAcs(init);
    setRunning(false);
    setElapsed(0);
    setLog([]);
    setViols([]);
    setCmdInput('');
    setSelAC(null);
    setBenchResult(null);
    setSafetyPts(40);
    setPhrasPts(0);
  }, []);

  // re-init whenever scenario changes (mode switches do NOT reset)
  useEffect(() => { resetSim(scIdx); }, [scIdx, resetSim]);

  // game loop
  useEffect(() => {
    if (!running) return undefined;
    runRef.current = true;
    const id = setInterval(() => {
      const prev = acsRef.current;
      const next = advance(prev, scenario);
      const v = detectViolations(next, scenario.sep);
      if (v.length > 0) safetyRef.current = Math.max(0, safetyRef.current - 0.15);
      acsRef.current = next;
      elapsedRef.current += 1;
      const acsByCs = Object.fromEntries(next.map((a) => [a.cs, a]));
      const nextGround = stepGround(groundRef.current, { layout, acsByCs, elapsed: elapsedRef.current, running: true });
      groundRef.current = nextGround;
      setAcs(next);
      setViols(v);
      setSafetyPts(safetyRef.current);
      setGround(nextGround);
      setElapsed((e) => e + 1);
    }, TICK);
    return () => { clearInterval(id); runRef.current = false; };
  }, [running, scenario, layout]);

  // auto-scroll comms log
  useEffect(() => { logEndRef.current?.scrollIntoView({ block: 'end' }); }, [log]);

  /* command application */
  const applyCommand = useCallback((raw) => {
    const text = raw.trim();
    if (!text) return;
    const ts = nowStr();
    const parsed = parseCommand(text, acsRef.current);
    if (parsed.error) {
      setLog((l) => [...l, { ts, tx: text, rx: parsed.error, err: true }]);
      return;
    }
    let readback = '';
    const next = acsRef.current.map((a) => {
      if (a.cs !== parsed.cs) return a;
      const na = { ...a };
      switch (parsed.type) {
        case 'alt': {
          const dir = parsed.value < na.altitude ? 'descend' : parsed.value > na.altitude ? 'climb' : 'maintain';
          na.targetAlt = parsed.value;
          readback = dir === 'maintain'
            ? `${na.cs}: maintain ${spokenAlt(parsed.value)}.`
            : `${na.cs}: ${dir} and maintain ${spokenAlt(parsed.value)}.`;
          break;
        }
        case 'hdg':
          na.heading = parsed.value; na.holding = false;
          readback = `${na.cs}: fly heading ${pad3(parsed.value)}.`;
          break;
        case 'spd':
          na.speed = parsed.value; na.holding = false;
          readback = `${na.cs}: speed ${parsed.value} knots.`;
          break;
        case 'ils':
          na.cleared = true; na.holding = false; na.assignedRunway = parsed.value;
          readback = `${na.cs}: cleared ILS runway ${parsed.value}, wilco.`;
          break;
        case 'tkof':
          na.onGround = false; na.holding = false; na.speed = 280; na.targetAlt = 5000;
          na.cleared = true; na.assignedRunway = parsed.value;
          readback = `${na.cs}: cleared for takeoff runway ${parsed.value}, rolling.`;
          break;
        case 'hold':
          na.holding = true;
          readback = `${na.cs}: hold present position.`;
          break;
        case 'ga':
          na.cleared = false; na.holding = false; na.targetAlt = na.targetAlt + 2000;
          na.heading = (na.heading + 180) % 360; goAroundRef.current += 1;
          readback = `${na.cs}: going around.`;
          break;
        default: break;
      }
      return na;
    });
    acsRef.current = next;
    setAcs(next);
    phrasRef.current = Math.min(20, phrasRef.current + 2);
    setPhrasPts(phrasRef.current);
    if (!cmdOrderRef.current.includes(parsed.cs)) cmdOrderRef.current = [...cmdOrderRef.current, parsed.cs];
    setLog((l) => [...l, { ts, tx: text, rx: readback, err: false }]);
  }, []);

  const submitCmd = () => { if (cmdInput.trim()) { applyCommand(cmdInput); setCmdInput(''); } };
  const selectAC = (cs) => { setSelAC(cs); setCmdInput(cs + ' '); inputRef.current?.focus(); };
  // ground/airborne units may be controllable scenario traffic or resident scenery
  const selectAnyCs = (cs) => { if (acsRef.current.some((a) => a.cs === cs)) selectAC(cs); else setSelAC(cs); };
  const onSelectGround = (u) => selectAnyCs(u.cs);
  const toggleRun = () => setRunning((r) => !r);
  const copyPrompt = () => {
    try {
      navigator.clipboard?.writeText(promptText).then(() => {
        setCopied(true); setTimeout(() => setCopied(false), 1500);
      }).catch(() => {});
    } catch (e) { /* clipboard unavailable */ }
  };
  const runEval = () => setBenchResult(evaluateResponse(benchText, scenario));

  /* derived live values */
  const violSet = new Set(viols.flatMap((v) => v.pair));
  const lowFuelAirborne = acs.filter((a) => a.lowFuel && a.active && !a.landed && !a.onGround).length;
  const efficiency = Math.max(0, 30 - 2 * viols.length - 2 * lowFuelAirborne - 5 * goAroundRef.current);
  const priorityLive = computePriority(acs, cmdOrderRef.current);
  const liveScores = {
    safety: safetyPts, efficiency, phraseology: phrasPts, priority: priorityLive,
    total: Math.round(safetyPts + efficiency + phrasPts + priorityLive),
  };
  liveScores.grade = gradeFor(liveScores.total);
  const nActive = acs.filter((a) => a.active).length;
  const nLanded = acs.filter((a) => a.landed).length;
  const nDeparted = acs.filter((a) => a.departed).length;

  /* ----- render helpers ----- */
  const ScoreBar = (s) => (
    <div style={{ display: 'flex', alignItems: 'stretch', gap: 8, ...panel }}>
      {[
        ['SAFETY', s.safety, 40, SCORE.safety],
        ['EFFICIENCY', s.efficiency, 30, SCORE.efficiency],
        ['PHRASEOLOGY', s.phraseology, 20, SCORE.phraseology],
        ['PRIORITY', s.priority, 10, SCORE.priority],
      ].map(([label, val, max, col]) => (
        <div key={label} style={{ flex: 1, minWidth: 70 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: C.dim, marginBottom: 3 }}>
            <span>{label}</span><span style={{ color: col }}>{Math.round(val)}/{max}</span>
          </div>
          <div style={{ height: 8, background: '#0a1218', borderRadius: 4, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${Math.max(0, Math.min(1, val / max)) * 100}%`, background: col, transition: 'width .3s' }} />
          </div>
        </div>
      ))}
      <div style={{ textAlign: 'center', paddingLeft: 10, borderLeft: `1px solid ${C.border}`, minWidth: 66 }}>
        <div style={{ fontSize: 9, color: C.dim }}>TOTAL</div>
        <div style={{ fontSize: 19, fontWeight: 'bold', color: gradeColor(s.grade), lineHeight: 1.1 }}>{Math.round(s.total)}</div>
        <div style={{ fontSize: 13, fontWeight: 'bold', color: gradeColor(s.grade) }}>{s.grade}</div>
      </div>
    </div>
  );

  const renderTag = (a, px, py, color) => (
    <g>
      <text x={px + 10} y={py - 3} fill={color} fontSize={9} fontFamily={FONT}>{a.cs}</text>
      <text x={px + 10} y={py + 6} fill={C.dim} fontSize={8} fontFamily={FONT}>{Math.round(a.altitude)}↕ {Math.round(a.speed)}kt</text>
      {(a.emergency || a.lowFuel) && (
        <text x={px + 10} y={py + 15} fill={a.emergency ? C.red : C.amber} fontSize={8} fontFamily={FONT}>
          {a.emergency ? '⚡EMRG' : '⚠FUEL'}
        </text>
      )}
    </g>
  );

  const renderAircraft = (a) => {
    const px = a.svgX, py = a.svgY;
    if (a.landed) {
      return (
        <g key={a.cs}>
          <circle cx={px} cy={py} r={2.5} fill={C.dim} opacity={0.6} />
          <text x={px + 6} y={py + 3} fill={C.dim} fontSize={8} fontFamily={FONT} opacity={0.75}>{a.cs} ✓</text>
        </g>
      );
    }
    if (!a.active) return null;
    const inViol = violSet.has(a.cs);
    let color = a.color;
    if (a.status === 'dep' && !a.onGround) color = C.dep;
    if (a.lowFuel) color = C.amber;
    if (a.emergency) color = C.red;
    if (inViol) color = C.red;

    if (a.onGround) {
      return (
        <g key={a.cs} style={{ cursor: 'pointer' }} onClick={() => selectAC(a.cs)}>
          {selAC === a.cs && <circle cx={px} cy={py} r={11} fill="none" stroke={C.green} strokeWidth={1} strokeDasharray="3 2" />}
          <rect x={px - 3.5} y={py - 3.5} width={7} height={7} fill={C.dep} stroke="#000" strokeWidth={0.5} />
          {renderTag(a, px, py, C.dep)}
        </g>
      );
    }

    const r = (a.heading * Math.PI) / 180;
    const tip = { x: px + Math.sin(r) * 7, y: py - Math.cos(r) * 7 };
    const wl = 7 * 0.55;
    const lA = r + 2.5, rA = r - 2.5;
    const left = { x: px + Math.sin(lA) * wl, y: py - Math.cos(lA) * wl };
    const right = { x: px + Math.sin(rA) * wl, y: py - Math.cos(rA) * wl };
    const tri = `${tip.x},${tip.y} ${left.x},${left.y} ${right.x},${right.y}`;
    const trailPts = a.trail.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');

    return (
      <g key={a.cs} style={{ cursor: 'pointer' }} onClick={() => selectAC(a.cs)}>
        {a.trail.length > 1 && <polyline points={trailPts} fill="none" stroke={color} strokeWidth={1} opacity={0.25} />}
        {selAC === a.cs && <circle cx={px} cy={py} r={13} fill="none" stroke={C.green} strokeWidth={1} strokeDasharray="3 2" />}
        {(a.emergency || inViol) && (
          <circle cx={px} cy={py} r={10} fill="none" stroke={C.red} strokeWidth={1} opacity={0.5} style={{ animation: 'pulse 1s infinite' }} />
        )}
        <polygon points={tri} fill={color} stroke={color} strokeWidth={1} strokeLinejoin="round" />
        {renderTag(a, px, py, color)}
      </g>
    );
  };

  const renderRadar = () => (
    <svg width={480} height={480} style={{ background: C.radarBg, border: `1px solid ${C.border}`, borderRadius: 6, display: 'block', flexShrink: 0 }}>
      {[5, 10, 15, 20].map((rg) => {
        const rad = rg * PPM;
        const lx = CX + rad * 0.707, ly = CY - rad * 0.707;
        return (
          <g key={rg}>
            <circle cx={CX} cy={CY} r={rad} fill="none" stroke={C.ring} strokeWidth={1} />
            <text x={lx} y={ly} fill={C.dim} fontSize={9} fontFamily={FONT} textAnchor="middle">{rg}</text>
          </g>
        );
      })}
      <line x1={0} y1={CY} x2={480} y2={CY} stroke={C.ring} strokeWidth={1} />
      <line x1={CX} y1={0} x2={CX} y2={480} stroke={C.ring} strokeWidth={1} />
      {scenario.airport === 'KDEN' && (
        <g>
          <ellipse cx={136} cy={305} rx={65} ry={90} fill="rgba(245,158,11,0.05)" stroke={C.amber} strokeWidth={1.5} strokeDasharray="6 4" opacity={0.7} />
          <text x={136} y={305} fill={C.amber} fontSize={9} fontFamily={FONT} textAnchor="middle" opacity={0.7}>CB</text>
        </g>
      )}
      <g>
        <circle cx={CX} cy={CY} r={14} fill="none" stroke={C.airport} strokeWidth={1} opacity={0.4} />
        {(() => {
          const groups = {};
          scenario.runways.forEach((id) => { const h = runwayHeading(id); (groups[h] = groups[h] || []).push(id); });
          const half = 11;
          const out = [];
          Object.entries(groups).forEach(([h, ids]) => {
            const r = (Number(h) * Math.PI) / 180;
            const d = { x: Math.sin(r), y: -Math.cos(r) }, p = { x: Math.cos(r), y: Math.sin(r) };
            ids.forEach((id, i) => {
              const off = (i - (ids.length - 1) / 2) * 3.4;
              const cx = CX + p.x * off, cy = CY + p.y * off;
              out.push(<line key={id} x1={cx - d.x * half} y1={cy - d.y * half} x2={cx + d.x * half} y2={cy + d.y * half}
                stroke={C.airport} strokeWidth={2} strokeLinecap="round" />);
            });
          });
          return out;
        })()}
        <text x={CX + 17} y={CY + 16} fill={C.airport} fontSize={11} fontFamily={FONT}>{scenario.airport}</text>
      </g>
      {acs.map((a) => renderAircraft(a))}
    </svg>
  );

  const renderAtis = () => (
    <div style={{ background: '#06100c', border: `1px solid ${C.border}`, borderRadius: 4, padding: '8px 10px', fontFamily: FONT }}>
      <div style={{ color: C.green, fontSize: 10, marginBottom: 4, letterSpacing: 1 }}>▌ ATIS · {scenario.airport}</div>
      <div style={{ color: C.amber, fontSize: 10.5, marginBottom: 4, wordBreak: 'break-word' }}>{scenario.wx}</div>
      <div style={{ color: C.text, fontSize: 11, lineHeight: 1.5 }}>{scenario.atis}</div>
    </div>
  );

  const renderLiveTraffic = () => (
    <div style={{ ...panel, padding: 0, overflow: 'hidden' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr style={{ color: C.dim, fontSize: 9, textAlign: 'left' }}>
            <th style={thS}>CS</th><th style={thS}>TYPE</th><th style={thS}>ALT</th>
            <th style={thS}>SPD</th><th style={thS}>HDG</th><th style={thS}>STATUS</th>
          </tr>
        </thead>
        <tbody>
          {acs.map((a) => (
            <tr key={a.cs} onClick={() => selectAC(a.cs)}
              style={{ cursor: 'pointer', background: selAC === a.cs ? 'rgba(0,229,160,0.08)' : 'transparent', borderTop: `1px solid ${C.border}` }}>
              <td style={{ ...tdS, color: a.color, fontWeight: 'bold' }}>{a.cs}</td>
              <td style={tdS}>{a.type}</td>
              <td style={tdS}>{a.onGround ? 'GND' : Math.round(a.altitude)}</td>
              <td style={tdS}>{Math.round(a.speed)}</td>
              <td style={tdS}>{pad3(a.heading)}</td>
              <td style={{ ...tdS, color: statusColor(a), fontWeight: 'bold' }}>{statusLabel(a)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  const renderInfoTab = () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ ...panel, flex: '1 1 320px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
            <h2 style={{ margin: 0, color: C.green, fontSize: 16 }}>{scenario.name}</h2>
            <span style={diffBadge(scenario.difficulty)}>{scenario.difficulty}</span>
          </div>
          <p style={{ color: C.text, fontSize: 12, lineHeight: 1.5 }}>{scenario.description}</p>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 11, color: C.dim }}>
            <span>AIRPORT <b style={{ color: C.text }}>{scenario.airport}</b></span>
            <span>AIRCRAFT <b style={{ color: C.text }}>{scenario.aircraft.length}</b></span>
            <span>SEP STD <b style={{ color: C.text }}>{scenario.sep}NM</b></span>
            <span>TIME <b style={{ color: C.text }}>{Math.floor(scenario.timeLimitSecs / 60)}min</b></span>
          </div>
        </div>
        <div style={{ ...panel, flex: '1 1 320px' }}>
          {renderAtis()}
          <div style={{ marginTop: 10 }}>
            <div style={{ color: C.green, fontSize: 11, marginBottom: 6 }}>▌ OBJECTIVES</div>
            <ol style={{ margin: 0, paddingLeft: 18, color: C.text, fontSize: 11.5, lineHeight: 1.7 }}>
              {scenario.objectives.map((o, i) => <li key={i}>{o}</li>)}
            </ol>
          </div>
        </div>
      </div>
      <div style={panel}>
        <div style={{ color: C.green, fontSize: 11, marginBottom: 8 }}>▌ TRAFFIC TABLE</div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr style={{ color: C.dim, fontSize: 9, textAlign: 'left' }}>
                <th style={thS}>CALLSIGN</th><th style={thS}>TYPE</th><th style={thS}>ALT</th><th style={thS}>SPD</th>
                <th style={thS}>HDG</th><th style={thS}>STATUS</th><th style={thS}>FUEL</th><th style={thS}>RWY</th>
              </tr>
            </thead>
            <tbody>
              {scenario.aircraft.map((a, i) => (
                <tr key={a.cs} style={{ borderTop: `1px solid ${C.border}` }}>
                  <td style={{ ...tdS, color: AC_COLORS[i % AC_COLORS.length], fontWeight: 'bold' }}>{a.cs}</td>
                  <td style={tdS}>{a.type}</td>
                  <td style={tdS}>{a.onGround ? 'GND' : a.altitude + 'ft'}</td>
                  <td style={tdS}>{a.onGround ? '—' : a.speed}</td>
                  <td style={tdS}>{pad3(a.heading)}</td>
                  <td style={{ ...tdS, color: a.emergency ? C.red : a.lowFuel ? C.amber : a.status === 'dep' ? C.dep : C.text }}>
                    {a.emergency ? 'EMERGENCY' : a.lowFuel ? 'LOW FUEL' : a.status === 'dep' ? 'DEPARTURE' : 'ARRIVAL'}
                  </td>
                  <td style={tdS}>{a.fuelMins >= 999 ? '—' : a.fuelMins + 'm'}</td>
                  <td style={tdS}>{a.assignedRunway}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <button style={{ ...btnPrimary, flex: '1 1 200px' }} onClick={() => setMode('game')}>▶ PLAY SCENARIO</button>
        <button style={{ ...btnAccent, flex: '1 1 200px' }} onClick={() => setMode('benchmark')}>⚡ BENCHMARK MODE</button>
      </div>
    </div>
  );

  const renderCommandBar = () => (
    <div style={panel}>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          ref={inputRef}
          value={cmdInput}
          onChange={(e) => setCmdInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submitCmd(); }}
          placeholder="e.g. UAL1234 descend and maintain 3000  ·  AAL100 cleared takeoff 10R"
          style={{ flex: 1, minWidth: 0, background: '#05100b', border: `1px solid ${C.border}`, color: C.green, fontFamily: FONT, fontSize: 12, padding: '8px 10px', borderRadius: 4, outline: 'none' }}
        />
        <button onClick={submitCmd} style={{ background: C.green, color: '#031b12', border: 'none', borderRadius: 4, padding: '0 16px', fontFamily: FONT, fontWeight: 'bold', cursor: 'pointer' }}>TX</button>
      </div>
      <div style={{ color: C.dim, fontSize: 9.5, marginTop: 6, lineHeight: 1.5 }}>
        DESCEND/CLIMB [alt] · TURN LEFT/RIGHT [hdg] · SPEED [kt] · CLEARED ILS [rwy] · CLEARED TAKEOFF [rwy] · HOLD
      </div>
    </div>
  );

  const renderCommsLog = (height = 170) => (
    <div style={panel}>
      <div style={{ color: C.green, fontSize: 10, marginBottom: 6 }}>▌ COMMS LOG</div>
      <div style={{ height, overflowY: 'auto', fontSize: 11, lineHeight: 1.6 }}>
        {log.length === 0 && <div style={{ color: C.dim }}>No transmissions yet…</div>}
        {log.map((e, i) => (
          <div key={i} style={{ marginBottom: 4 }}>
            <div style={{ color: C.text }}><span style={{ color: C.dim }}>[{e.ts}]</span> → {e.tx}</div>
            <div style={{ color: e.err ? C.red : C.green, paddingLeft: 12 }}>← {e.rx}</div>
          </div>
        ))}
        <div ref={logEndRef} />
      </div>
    </div>
  );

  const renderGameTab = () => (
    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {renderRadar()}
        {ScoreBar(liveScores)}
      </div>
      <div style={{ flex: '1 1 360px', display: 'flex', flexDirection: 'column', gap: 10, minWidth: 300 }}>
        {renderAtis()}
        {renderLiveTraffic()}
        {renderCommandBar()}
        {renderCommsLog(170)}
      </div>
    </div>
  );

  const GROUND_PHASE = {
    park: ['AT GATE', C.dim], loop: ['TAXI', C.blue], taxiOut: ['TAXI OUT', C.amber],
    taxiIn: ['TAXI IN', C.green], hold: ['HOLD SHORT', '#d6b53a'], roll: ['DEPARTING', C.dep],
    rollout: ['LANDING', C.green],
  };

  const renderAirportTab = () => {
    const order = ['roll', 'rollout', 'hold', 'taxiOut', 'taxiIn', 'loop', 'park'];
    const active = ground.filter((u) => u.phase !== 'park');
    const parkedN = ground.filter((u) => u.phase === 'park').length;
    const sorted = [...ground].sort((a, b) => order.indexOf(a.phase) - order.indexOf(b.phase));
    const legend = [
      [C.dep, '■', 'Departure'], [C.green, '■', 'Arrival'], ['#5bc8f5', '■', 'Resident ramp'],
      ['#d6b53a', '▬', 'Hold short'], [C.blue, '▲', 'Airborne (on final)'],
    ];
    return (
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 600px', minWidth: 320, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <AirportDiagram layout={layout} units={ground} airborne={acs} selected={selAC} onSelect={selectAnyCs} scenario={scenario} />
          <div style={{ ...panel, display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: 10, color: C.dim, alignItems: 'center' }}>
            {legend.map(([col, sym, label]) => (
              <span key={label}><span style={{ color: col }}>{sym}</span> {label}</span>
            ))}
            <span style={{ marginLeft: 'auto', color: C.text }}>
              GATES <b style={{ color: C.green }}>{parkedN}</b>/{layout.gates.length} · TAXIING <b style={{ color: C.amber }}>{active.length}</b>
            </span>
          </div>
        </div>
        <div style={{ flex: '1 1 300px', display: 'flex', flexDirection: 'column', gap: 10, minWidth: 280, maxWidth: 440 }}>
          {renderAtis()}
          {renderCommandBar()}
          <div style={{ ...panel, padding: 0, overflow: 'hidden' }}>
            <div style={{ color: C.green, fontSize: 10, padding: '8px 8px 6px' }}>▌ GROUND MOVEMENT</div>
            <div style={{ maxHeight: 190, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <tbody>
                  {sorted.map((u) => {
                    const [label, col] = GROUND_PHASE[u.phase] || ['—', C.dim];
                    return (
                      <tr key={u.id} onClick={() => onSelectGround(u)}
                        style={{ cursor: 'pointer', background: selAC === u.cs ? 'rgba(0,229,160,0.08)' : 'transparent', borderTop: `1px solid ${C.border}` }}>
                        <td style={{ ...tdS, color: u.color, fontWeight: 'bold' }}>{u.label}</td>
                        <td style={tdS}>{u.type}</td>
                        <td style={{ ...tdS, color: col, fontWeight: 'bold' }}>{label}</td>
                        <td style={{ ...tdS, color: C.dim }}>{u.runwayId ? 'RWY ' + u.runwayId : 'gate ' + (u.gate + 1)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
          {renderCommsLog(150)}
        </div>
      </div>
    );
  };

  const renderBenchResult = () => {
    const r = benchResult;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {ScoreBar({ safety: r.safety, efficiency: r.eff, phraseology: r.phras, priority: r.priority, total: r.total, grade: r.grade })}
        <div style={{ display: 'flex', gap: 14, fontSize: 11, color: C.text, flexWrap: 'wrap' }}>
          <span>COVERAGE <b style={{ color: r.addressedCount === r.n ? C.green : C.amber }}>{r.addressedCount}/{r.n}</b></span>
          {r.missing.length > 0 && <span style={{ color: C.red }}>MISSING: {r.missing.join(', ')}</span>}
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr style={{ color: C.dim, fontSize: 9, textAlign: 'left' }}>
              <th style={thS}>CALLSIGN</th><th style={thS}>PTS</th><th style={thS}>NOTES</th>
            </tr>
          </thead>
          <tbody>
            {r.perAC.map((p) => (
              <tr key={p.cs} style={{ borderTop: `1px solid ${C.border}` }}>
                <td style={{ ...tdS, color: p.addressed ? C.text : C.red, fontWeight: 'bold' }}>{p.cs}</td>
                <td style={{ ...tdS, color: p.points === p.max ? C.green : p.addressed ? C.amber : C.red }}>{p.points}/{p.max}</td>
                <td style={{ ...tdS, color: C.dim, fontSize: 10 }}>{p.notes}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  const renderBenchTab = () => (
    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      <div style={{ ...panel, flex: '1 1 420px', minWidth: 320 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, gap: 8 }}>
          <div style={{ color: C.green, fontSize: 12 }}>▌ Benchmark Prompt — {scenario.name}</div>
          <button onClick={copyPrompt} style={btnSmall}>{copied ? '✓ COPIED' : 'COPY'}</button>
        </div>
        <pre style={{ margin: 0, maxHeight: 580, overflow: 'auto', background: '#05100b', border: `1px solid ${C.border}`, borderRadius: 4, padding: 12, color: C.text, fontSize: 11, lineHeight: 1.45, whiteSpace: 'pre', fontFamily: FONT }}>{promptText}</pre>
      </div>
      <div style={{ ...panel, flex: '1 1 420px', minWidth: 320, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ color: C.green, fontSize: 12 }}>▌ Evaluate AI Response</div>
        <label style={{ color: C.dim, fontSize: 11 }}>Paste AI Model Response</label>
        <textarea
          value={benchText}
          onChange={(e) => setBenchText(e.target.value)}
          rows={8}
          placeholder={'UAL1234, Chicago Approach, descend and maintain three thousand...\nAAL567, Chicago Approach, reduce speed one eight zero knots...'}
          style={{ width: '100%', boxSizing: 'border-box', background: '#05100b', border: `1px solid ${C.border}`, color: C.text, fontFamily: FONT, fontSize: 11.5, padding: 10, borderRadius: 4, resize: 'vertical', outline: 'none' }}
        />
        <button onClick={runEval} style={{ ...btnPrimary, padding: 10 }}>EVALUATE RESPONSE</button>
        {benchResult && renderBenchResult()}
      </div>
    </div>
  );

  /* ----- main render ----- */
  return (
    <div style={{ minHeight: '100vh', background: C.bg, color: C.text, fontFamily: FONT, padding: 14 }}>
      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        *{box-sizing:border-box}
        ::-webkit-scrollbar{width:8px;height:8px}
        ::-webkit-scrollbar-thumb{background:#152535;border-radius:4px}
        ::-webkit-scrollbar-track{background:#0a1218}
        select option{background:#0c1520;color:#8fb8a8}
        input::placeholder,textarea::placeholder{color:#2f5648}
      `}</style>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 14, borderBottom: `1px solid ${C.border}`, paddingBottom: 12 }}>
        <div style={{ color: C.green, fontWeight: 'bold', fontSize: 16, letterSpacing: 1 }}>
          ◎ ATC<span style={{ color: C.dim, fontWeight: 'normal' }}> // RADAR BENCH</span>
        </div>
        <select
          value={scIdx}
          onChange={(e) => setScIdx(Number(e.target.value))}
          style={{ background: C.panel, color: C.text, border: `1px solid ${C.border}`, borderRadius: 4, padding: '6px 8px', fontFamily: FONT, fontSize: 12 }}
        >
          {SCENARIOS.map((s, i) => <option key={s.id} value={i}>{i + 1}. {s.name}</option>)}
        </select>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {[['info', '📋 INFO'], ['game', '▶ RADAR'], ['airport', '🛬 GROUND'], ['benchmark', '⚡ BENCH']].map(([m, label]) => (
            <button key={m} onClick={() => setMode(m)} style={tabStyle(mode === m)}>{label}</button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        {(mode === 'game' || mode === 'airport') && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            {viols.length > 0 && (
              <span style={{ color: C.red, fontWeight: 'bold', fontSize: 12, animation: 'pulse 1s infinite' }}>⚠ SEPARATION ({viols.length})</span>
            )}
            <span style={{ fontSize: 12, color: C.text }}>⏱ {formatTime((elapsed * TICK) / 1000)}</span>
            <span style={{ fontSize: 11, color: C.dim }}>
              ACT <b style={{ color: C.green }}>{nActive}</b> · LDG <b style={{ color: C.blue }}>{nLanded}</b> · DEP <b style={{ color: C.dep }}>{nDeparted}</b>
            </span>
            <button onClick={toggleRun} style={{ ...btnSmall, color: running ? C.amber : C.green, borderColor: running ? C.amber : C.green }}>
              {running ? '❚❚ PAUSE' : '▶ START'}
            </button>
            <button onClick={() => resetSim(scIdx)} style={{ ...btnSmall, color: C.dim, borderColor: C.border }}>↺ RESET</button>
          </div>
        )}
      </div>

      {mode === 'info' && renderInfoTab()}
      {mode === 'game' && renderGameTab()}
      {mode === 'airport' && renderAirportTab()}
      {mode === 'benchmark' && renderBenchTab()}
    </div>
  );
}
