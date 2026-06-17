import React from 'react';

/* ============================================================
   AIRPORT SURFACE DIAGRAM + GROUND OPERATIONS
   Generates a schematic airport layout (runways at true heading,
   taxiways, terminal concourse with numbered gates) from a
   scenario's runway list, and animates aircraft on the ground:
   parked at gates, taxiing, holding short, lining up, taking off,
   and taxiing in after landing.
   ============================================================ */

/* ----------------------------- geometry constants ----------------------------- */
export const DIAG = { W: 880, H: 600 };
const RX = 440, RY = 312;           // airport reference point (screen)
const DPPM = 95;                    // surface-view pixels per nautical mile
const RW_LEN = 2.4 * DPPM;          // ~2.4 nm runways
const HALF = RW_LEN / 2;
const RW_W = 15;                    // runway width (px)
const RW_SPACING = 46;              // parallel runway separation (px)
const APRON_V = 134;                // apron lane offset from field center
const TERM_V = 190;                 // terminal concourse offset
const CONC_HALF = 150;              // half-length of concourse
const NGATES = 11;

const TAXI_SPD = 3.0;               // px/tick

const G = {
  field: '#05110d', apron: '#0c1a16', taxi: '#b89a32', taxiCase: '#2c2913',
  rw: '#1b2630', rwEdge: '#435d6b', rwLine: '#d7e2ea', rwShoulder: '#212c35',
  term: '#152330', termEdge: '#2c4150', gate: '#243a48', jetway: '#35506220',
  text: '#7fae9e', dim: '#3a6a58', hold: '#d6b53a',
  green: '#00e5a0', amber: '#f59e0b', red: '#ff3355', blue: '#4ab8f4',
};

/* decorative resident fleet that lives on the ramp (not part of scoring) */
const SCENERY_FLEET = [
  ['UAL', 'B739', '#5bc8f5'], ['AAL', 'A319', '#f43f5e'], ['DAL', 'B752', '#a855f7'],
  ['SWA', 'B737', '#fb923c'], ['ASA', 'B739', '#10b981'], ['JBU', 'A320', '#06b6d4'],
  ['FFT', 'A20N', '#84cc16'], ['SKW', 'CRJ9', '#f5a623'], ['NKS', 'A21N', '#fde047'],
  ['WJA', 'B738', '#22d3ee'],
];

/* ----------------------------- runway helpers ----------------------------- */
export function runwayHeading(id) { return (parseInt(id, 10) % 36) * 10; }
export function reciprocal(id) {
  const n = parseInt(id, 10);
  const side = id.replace(/^\d+/, '');
  const rn = ((n + 18 - 1) % 36) + 1;
  const rs = side === 'L' ? 'R' : side === 'R' ? 'L' : side;
  return String(rn).padStart(2, '0') + rs;
}
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const angleOf = (v) => (Math.atan2(v.x, -v.y) * 180) / Math.PI;   // compass heading for a nose-up glyph
const screenAngle = (v) => (Math.atan2(v.y, v.x) * 180) / Math.PI; // rotation to align an +x shape with v
const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);
const polyStr = (pts) => pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');

const fp = (L, u, v) => ({ x: RX + L.d0.x * u + L.p0.x * v, y: RY + L.d0.y * u + L.p0.y * v });
const toFrame = (L, pt) => ({
  u: (pt.x - RX) * L.d0.x + (pt.y - RY) * L.d0.y,
  v: (pt.x - RX) * L.p0.x + (pt.y - RY) * L.p0.y,
});

/* ----------------------------- layout builder ----------------------------- */
export function buildLayout(sc) {
  // group runways by numeric heading so parallels share an axis
  const groups = {};
  sc.runways.forEach((id) => {
    const h = runwayHeading(id);
    (groups[h] = groups[h] || []).push(id);
  });

  const runways = [];
  Object.entries(groups).forEach(([h, ids]) => {
    const hd = Number(h);
    const r = (hd * Math.PI) / 180;
    const d = { x: Math.sin(r), y: -Math.cos(r) };
    const p = { x: Math.cos(r), y: Math.sin(r) };
    const n = ids.length;
    ids.forEach((id, i) => {
      const off = (i - (n - 1) / 2) * RW_SPACING;
      const cx = RX + p.x * off, cy = RY + p.y * off;
      runways.push({
        id, recip: reciprocal(id), heading: hd, d, p, off,
        cx, cy,
        thr: { x: cx - d.x * HALF, y: cy - d.y * HALF },
        far: { x: cx + d.x * HALF, y: cy + d.y * HALF },
      });
    });
  });

  // primary (departure) runway defines the terminal-aligned frame
  const depId = (sc.aircraft.find((a) => a.status === 'dep') || {}).assignedRunway
    || sc.runways[sc.runways.length - 1];
  const depRwy = runways.find((rw) => rw.id === depId) || runways[0];
  const d0 = depRwy.d, p0 = depRwy.p;
  const L = { runways, d0, p0, depRwy };

  // store each runway's perpendicular offset within the terminal frame
  runways.forEach((rw) => { rw.voff = toFrame(L, { x: rw.cx, y: rw.cy }).v; });

  // gates along the concourse, facing back toward the runways
  const gateRot = angleOf(p0);
  L.gates = Array.from({ length: NGATES }, (_, i) => {
    const u = -CONC_HALF + 16 + ((CONC_HALF - 16) * 2 * i) / (NGATES - 1);
    const pos = fp(L, u, TERM_V - 17);
    return { idx: i, u, x: pos.x, y: pos.y, rot: gateRot };
  });

  // terminal concourse (a rounded bar parallel to the runways)
  const tc = fp(L, 0, TERM_V);
  L.terminal = { cx: tc.x, cy: tc.y, rot: screenAngle(d0), len: CONC_HALF * 2 };

  // apron lane endpoints (taxiway in front of the gates)
  L.apron = { a: fp(L, -CONC_HALF, APRON_V), b: fp(L, CONC_HALF, APRON_V) };

  // scenery taxi loop on the ramp
  L.loop = [
    fp(L, -CONC_HALF + 24, APRON_V),
    fp(L, CONC_HALF - 24, APRON_V),
    fp(L, CONC_HALF - 24, APRON_V - 30),
    fp(L, -CONC_HALF + 24, APRON_V - 30),
  ];

  L.depRwyId = depRwy.id;
  L.arrRwyIds = [...new Set(sc.aircraft.filter((a) => a.status === 'arr').map((a) => a.assignedRunway))];
  return L;
}

/* ----------------------------- route builders ----------------------------- */
function holdShortOf(L, rw) {
  const side = APRON_V > rw.voff ? 1 : -1;
  const d = RW_W / 2 + 12;
  return { x: rw.thr.x + L.p0.x * side * d, y: rw.thr.y + L.p0.y * side * d };
}
function depRoutes(L, gate, rw) {
  const hs = holdShortOf(L, rw);
  const uThr = clamp(toFrame(L, rw.thr).u, -CONC_HALF, CONC_HALF);
  return {
    taxi: [{ x: gate.x, y: gate.y }, fp(L, gate.u, APRON_V), fp(L, uThr, APRON_V), hs],
    roll: [hs, { x: rw.thr.x, y: rw.thr.y }, { x: rw.far.x, y: rw.far.y }],
  };
}
function arrRoutes(L, gate, rw) {
  const td = { x: rw.thr.x + (rw.far.x - rw.thr.x) * 0.1, y: rw.thr.y + (rw.far.y - rw.thr.y) * 0.1 };
  const exit = { x: rw.thr.x + (rw.far.x - rw.thr.x) * 0.82, y: rw.thr.y + (rw.far.y - rw.thr.y) * 0.82 };
  const uExit = clamp(toFrame(L, exit).u, -CONC_HALF, CONC_HALF);
  return {
    rollout: [td, exit],
    taxi: [exit, fp(L, uExit, APRON_V), fp(L, gate.u, APRON_V), { x: gate.x, y: gate.y }],
  };
}

/* ----------------------------- unit builder ----------------------------- */
export function buildUnits(sc, L) {
  const units = [];
  const gateUsed = new Set();

  // departures from the scenario start parked at the inboard gates
  const deps = sc.aircraft.filter((a) => a.status === 'dep');
  deps.forEach((a, i) => {
    const gate = L.gates[i];
    gateUsed.add(gate.idx);
    const rw = L.runways.find((r) => r.id === a.assignedRunway) || L.depRwy;
    units.push({
      id: 'dep-' + a.cs, cs: a.cs, type: a.type, color: '#fbbf24', role: 'dep',
      phase: 'park', gate: gate.idx, runwayId: rw.id, routes: depRoutes(L, gate, rw),
      seg: 0, t: 0, x: gate.x, y: gate.y, rot: gate.rot,
      startTick: 12 + i * 26, label: a.cs,
    });
  });

  // resident scenery parked at the outboard gates
  let fleetI = 0;
  for (let gi = L.gates.length - 1; gi >= 0 && units.filter((u) => u.role === 'scenery' && u.phase === 'park').length < 6; gi--) {
    if (gateUsed.has(gi)) continue;
    const gate = L.gates[gi];
    gateUsed.add(gi);
    const [code, type, color] = SCENERY_FLEET[fleetI % SCENERY_FLEET.length];
    fleetI++;
    units.push({
      id: 'park-' + gi, cs: code + (210 + gi * 7), type, color, role: 'scenery',
      phase: 'park', gate: gi, x: gate.x, y: gate.y, rot: gate.rot, label: code + (210 + gi * 7),
    });
  }

  // a couple of jets taxiing around the ramp loop
  for (let k = 0; k < 2; k++) {
    const [code, type, color] = SCENERY_FLEET[(fleetI + k) % SCENERY_FLEET.length];
    const seg = k === 0 ? 0 : 1;
    units.push({
      id: 'taxi-' + k, cs: code + (480 + k * 13), type, color, role: 'sceneryTaxi',
      phase: 'loop', route: L.loop, seg, t: k === 0 ? 0.1 : 0.6,
      x: L.loop[seg].x, y: L.loop[seg].y, rot: 0, label: code + (480 + k * 13),
    });
  }

  return units;
}

export function buildAirport(sc) {
  const layout = buildLayout(sc);
  return { layout, units: buildUnits(sc, layout) };
}

/* ----------------------------- movement ----------------------------- */
function advanceAlong(route, seg, t, step, loop) {
  let s = seg, tt = t, st = step;
  while (st > 0) {
    if (s >= route.length - 1) {
      if (loop) { s = 0; tt = 0; } else { tt = 1; break; }
    }
    const a = route[s], b = route[s + 1];
    const segLen = dist(a, b) || 1;
    const remain = (1 - tt) * segLen;
    if (st < remain) { tt += st / segLen; st = 0; } else { st -= remain; s++; tt = 0; }
  }
  const ai = Math.min(s, route.length - 2);
  const a = route[ai], b = route[ai + 1];
  const pos = { x: a.x + (b.x - a.x) * tt, y: a.y + (b.y - a.y) * tt };
  const rot = angleOf({ x: b.x - a.x, y: b.y - a.y });
  const done = !loop && s >= route.length - 1 && tt >= 1;
  return { seg: s, t: tt, pos, rot, done };
}

function stepUnit(u, ctx) {
  const { running, elapsed, acsByCs } = ctx;
  if (u.phase === 'park') {
    if (u.role === 'dep' && running && elapsed >= u.startTick) {
      return { ...u, phase: 'taxiOut', route: u.routes.taxi, seg: 0, t: 0 };
    }
    return u;
  }
  if (!running) return u;

  if (u.phase === 'loop') {
    const r = advanceAlong(u.route, u.seg, u.t, TAXI_SPD, true);
    return { ...u, seg: r.seg, t: r.t, x: r.pos.x, y: r.pos.y, rot: r.rot };
  }
  if (u.phase === 'taxiOut' || u.phase === 'taxiIn') {
    const r = advanceAlong(u.route, u.seg, u.t, TAXI_SPD, false);
    if (r.done) {
      if (u.phase === 'taxiOut') return { ...u, phase: 'hold', x: r.pos.x, y: r.pos.y, rot: r.rot };
      return { ...u, phase: 'park', x: r.pos.x, y: r.pos.y, rot: r.rot };
    }
    return { ...u, seg: r.seg, t: r.t, x: r.pos.x, y: r.pos.y, rot: r.rot };
  }
  if (u.phase === 'hold') {
    const ac = acsByCs[u.cs];
    if (ac && ac.onGround === false) return { ...u, phase: 'roll', route: u.routes.roll, seg: 0, t: 0 };
    return u;
  }
  if (u.phase === 'roll') {
    const prog = (u.seg + u.t) / (u.route.length - 1);
    const spd = 4 + 15 * prog;
    const r = advanceAlong(u.route, u.seg, u.t, spd, false);
    if (r.done) return { ...u, gone: true };
    return { ...u, seg: r.seg, t: r.t, x: r.pos.x, y: r.pos.y, rot: r.rot };
  }
  if (u.phase === 'rollout') {
    const prog = (u.seg + u.t) / (u.route.length - 1);
    const spd = 13 - 9 * prog;
    const r = advanceAlong(u.route, u.seg, u.t, spd, false);
    if (r.done) return { ...u, phase: 'taxiIn', route: u.taxiIn, seg: 0, t: 0 };
    return { ...u, seg: r.seg, t: r.t, x: r.pos.x, y: r.pos.y, rot: r.rot };
  }
  return u;
}

function freeGate(L, units) {
  const used = new Set(units.filter((u) => u.phase === 'park' || u.phase === 'taxiIn').map((u) => u.gate));
  const g = L.gates.find((gt) => !used.has(gt.idx));
  return g || L.gates[0];
}

function spawnArrival(L, a, units) {
  const rw = L.runways.find((r) => r.id === a.assignedRunway) || L.runways[0];
  const gate = freeGate(L, units);
  const routes = arrRoutes(L, gate, rw);
  const start = routes.rollout[0];
  return {
    id: 'arr-' + a.cs, cs: a.cs, type: a.type,
    color: a.emergency ? '#ff3355' : a.lowFuel ? '#f59e0b' : '#00e5a0',
    role: 'arr', phase: 'rollout', route: routes.rollout, taxiIn: routes.taxi,
    gate: gate.idx, runwayId: rw.id, seg: 0, t: 0, x: start.x, y: start.y,
    rot: angleOf(rw.d), label: a.cs,
  };
}

export function stepGround(units, ctx) {
  let out = units.map((u) => stepUnit(u, ctx));
  // spawn taxi-in for aircraft that have just landed
  Object.values(ctx.acsByCs).forEach((a) => {
    if (a.landed && a.status === 'arr' && !out.some((u) => u.role === 'arr' && u.cs === a.cs)) {
      out.push(spawnArrival(ctx.layout, a, out));
    }
  });
  return out.filter((u) => !u.gone);
}

/* ----------------------------- plane glyph ----------------------------- */
function typeScale(type) {
  if (/^(B74|B77|B78|A38|A35|A34|B75|A33|B76)/.test(type)) return 1.28;
  if (/^(CRJ|E1|E7|CR)/.test(type)) return 0.78;
  return 1.0;
}
function planeGlyph(key, x, y, rot, s, color, opts = {}) {
  const wing = `M ${-2 * s},${-1.2 * s} L ${-11.5 * s},${4 * s} L ${-9.5 * s},${4.8 * s} L ${-2 * s},${1.6 * s}
                L ${2 * s},${1.6 * s} L ${9.5 * s},${4.8 * s} L ${11.5 * s},${4 * s} L ${2 * s},${-1.2 * s} Z`;
  const tail = `M ${-1.3 * s},${7 * s} L ${-5 * s},${9.6 * s} L ${-4 * s},${10.2 * s} L ${-1.3 * s},${8.6 * s}
                L ${1.3 * s},${8.6 * s} L ${4 * s},${10.2 * s} L ${5 * s},${9.6 * s} L ${1.3 * s},${7 * s} Z`;
  return (
    <g key={key} transform={`translate(${x.toFixed(1)},${y.toFixed(1)}) rotate(${rot.toFixed(1)})`}
      opacity={opts.opacity ?? 1} style={opts.onClick ? { cursor: 'pointer' } : undefined} onClick={opts.onClick}>
      {opts.ring && <circle r={13 * s} fill="none" stroke={opts.ring} strokeWidth={1.2} strokeDasharray="3 2" />}
      {opts.alert && <circle r={11 * s} fill="none" stroke={G.red} strokeWidth={1} opacity={0.6} style={{ animation: 'pulse 1s infinite' }} />}
      <path d={wing} fill={color} stroke="#02100a" strokeWidth={0.5} strokeLinejoin="round" />
      <path d={tail} fill={color} stroke="#02100a" strokeWidth={0.5} strokeLinejoin="round" />
      <rect x={-1.7 * s} y={-11.5 * s} width={3.4 * s} height={20 * s} rx={1.7 * s} fill={color} stroke="#02100a" strokeWidth={0.5} />
      <circle cx={0} cy={-9.5 * s} r={1 * s} fill="#02100a" opacity={0.5} />
    </g>
  );
}

/* ----------------------------- runway renderer ----------------------------- */
function Runway({ rw, depId }) {
  const len = dist(rw.thr, rw.far);
  const ang = angleOf(rw.d) - 90; // long axis along +x after rotate
  const isDep = rw.id === depId;
  const dashes = [];
  const n = 9;
  for (let i = 1; i < n; i++) dashes.push(-len / 2 + (len * i) / n);
  return (
    <g transform={`translate(${rw.cx.toFixed(1)},${rw.cy.toFixed(1)}) rotate(${ang.toFixed(1)})`}>
      <rect x={-len / 2 - 2} y={-RW_W / 2 - 3} width={len + 4} height={RW_W + 6} fill={G.rwShoulder} rx={2} />
      <rect x={-len / 2} y={-RW_W / 2} width={len} height={RW_W} fill={G.rw} stroke={G.rwEdge} strokeWidth={1} />
      {/* centerline dashes */}
      {dashes.map((dx, i) => (
        <rect key={i} x={dx - 6} y={-0.9} width={12} height={1.8} fill={G.rwLine} opacity={0.8} />
      ))}
      {/* threshold bars */}
      {[-1, 1].map((sgn) => (
        <g key={sgn}>
          {[-3, -1.5, 0, 1.5, 3].map((o) => (
            <rect key={o} x={sgn * (len / 2 - 9)} y={o * 2.4 - 0.8} width={7} height={1.6} fill={G.rwLine} opacity={0.85} />
          ))}
        </g>
      ))}
      {isDep && <rect x={-len / 2} y={-RW_W / 2} width={len} height={RW_W} fill={G.green} opacity={0.05} />}
    </g>
  );
}

function RunwayLabels({ rw }) {
  // designator numbers read along the runway at each end
  const place = (pt, id, dir) => {
    const ang = angleOf(dir);
    const back = { x: pt.x - dir.x * 16, y: pt.y - dir.y * 16 };
    return (
      <text x={back.x} y={back.y} fill={G.rwLine} fontSize={11} fontWeight="bold"
        fontFamily="'Courier New', monospace" textAnchor="middle" dominantBaseline="central"
        transform={`rotate(${ang} ${back.x} ${back.y})`} opacity={0.92}>{id}</text>
    );
  };
  return (
    <g>
      {place(rw.thr, rw.id, rw.d)}
      {place(rw.far, rw.recip, { x: -rw.d.x, y: -rw.d.y })}
    </g>
  );
}

/* ----------------------------- airport diagram component ----------------------------- */
export function AirportDiagram({ layout: L, units, airborne = [], selected, onSelect, scenario }) {
  if (!L) return null;
  const phaseColor = { taxiOut: G.amber, taxiIn: G.green, hold: G.hold, roll: G.amber, rollout: G.green, loop: G.blue };

  // project a radar (nm) position onto the surface view
  const project = (a) => {
    const nx = (a.svgX - 240) / 13, ny = (240 - a.svgY) / 13;
    return { x: RX + nx * DPPM, y: RY - ny * DPPM };
  };

  return (
    <svg viewBox={`0 0 ${DIAG.W} ${DIAG.H}`} width="100%"
      style={{ background: G.field, border: `1px solid ${G.termEdge}`, borderRadius: 6, display: 'block', maxWidth: DIAG.W }}>
      <defs>
        <radialGradient id="fieldglow" cx="50%" cy="52%" r="60%">
          <stop offset="0%" stopColor="#0a1a14" />
          <stop offset="100%" stopColor="#05110d" />
        </radialGradient>
      </defs>
      <rect x={0} y={0} width={DIAG.W} height={DIAG.H} fill="url(#fieldglow)" />

      {/* taxiways: apron lane + connectors (casing then yellow centerline) */}
      <g strokeLinecap="round">
        <line x1={L.apron.a.x} y1={L.apron.a.y} x2={L.apron.b.x} y2={L.apron.b.y} stroke={G.taxiCase} strokeWidth={13} />
        {L.runways.map((rw) => {
          const hs = holdShortOf(L, rw);
          const uThr = clamp(toFrame(L, rw.thr).u, -CONC_HALF, CONC_HALF);
          const ap = fp(L, uThr, APRON_V);
          return <line key={'tc' + rw.id} x1={ap.x} y1={ap.y} x2={hs.x} y2={hs.y} stroke={G.taxiCase} strokeWidth={13} />;
        })}
        <line x1={L.apron.a.x} y1={L.apron.a.y} x2={L.apron.b.x} y2={L.apron.b.y} stroke={G.taxi} strokeWidth={1.4} strokeDasharray="7 5" opacity={0.65} />
        {L.runways.map((rw) => {
          const hs = holdShortOf(L, rw);
          const uThr = clamp(toFrame(L, rw.thr).u, -CONC_HALF, CONC_HALF);
          const ap = fp(L, uThr, APRON_V);
          return <line key={'ty' + rw.id} x1={ap.x} y1={ap.y} x2={hs.x} y2={hs.y} stroke={G.taxi} strokeWidth={1.4} strokeDasharray="7 5" opacity={0.6} />;
        })}
      </g>

      {/* runways */}
      {L.runways.map((rw) => <Runway key={rw.id} rw={rw} depId={L.depRwyId} />)}
      {L.runways.map((rw) => <RunwayLabels key={'lbl' + rw.id} rw={rw} />)}

      {/* hold-short bars */}
      {L.runways.map((rw) => {
        const hs = holdShortOf(L, rw);
        const a = angleOf(rw.d);
        return (
          <g key={'hs' + rw.id} transform={`translate(${hs.x},${hs.y}) rotate(${a})`}>
            <rect x={-7} y={-1.2} width={14} height={2.4} fill={G.hold} opacity={0.8} />
          </g>
        );
      })}

      {/* apron pad behind the gates (frame-coordinate quad, heading-robust) */}
      <polygon points={polyStr([
        fp(L, -(CONC_HALF + 14), APRON_V - 8), fp(L, CONC_HALF + 14, APRON_V - 8),
        fp(L, CONC_HALF + 14, TERM_V - 2), fp(L, -(CONC_HALF + 14), TERM_V - 2),
      ])} fill={G.apron} opacity={0.6} />
      {/* terminal concourse building */}
      <polygon points={polyStr([
        fp(L, -(CONC_HALF + 7), TERM_V), fp(L, CONC_HALF + 7, TERM_V),
        fp(L, CONC_HALF + 7, TERM_V + 24), fp(L, -(CONC_HALF + 7), TERM_V + 24),
      ])} fill={G.term} stroke={G.termEdge} strokeWidth={1} />
      {Array.from({ length: 10 }).map((_, i) => {
        const u = -CONC_HALF + 8 + (i * (CONC_HALF * 2 - 16)) / 9;
        const w = fp(L, u, TERM_V + 12);
        return <rect key={'w' + i} x={w.x - 2.4} y={w.y - 2.4} width={4.8} height={4.8} fill="#0b1822" />;
      })}
      {(() => {
        const c = fp(L, 0, TERM_V + 12);
        let rot = screenAngle(L.d0);
        if (rot > 90) rot -= 180; else if (rot < -90) rot += 180;
        return (
          <text x={c.x} y={c.y} fill={G.dim} fontSize={9} fontFamily="'Courier New', monospace"
            textAnchor="middle" dominantBaseline="central" letterSpacing={2}
            transform={`rotate(${rot} ${c.x} ${c.y})`}>{scenario ? scenario.airport + ' TERMINAL' : 'TERMINAL'}</text>
        );
      })()}
      {/* gates: stands + jetways + numbers */}
      {L.gates.map((g) => (
        <g key={'g' + g.idx}>
          <line x1={g.x} y1={g.y} x2={g.x + L.p0.x * 16} y2={g.y + L.p0.y * 16} stroke={G.termEdge} strokeWidth={2.5} opacity={0.85} />
          <circle cx={g.x} cy={g.y} r={2.2} fill="none" stroke={G.dim} strokeWidth={0.8} opacity={0.6} />
          <text x={g.x - L.p0.x * 11} y={g.y - L.p0.y * 11} fill={G.dim} fontSize={7.5}
            fontFamily="'Courier New', monospace" textAnchor="middle" dominantBaseline="central">{g.idx + 1}</text>
        </g>
      ))}

      {/* airborne traffic that is within the surface view (short final / departure) */}
      {airborne.filter((a) => a.active && !a.onGround && !a.landed).map((a) => {
        const pt = project(a);
        if (pt.x < -10 || pt.x > DIAG.W + 10 || pt.y < -10 || pt.y > DIAG.H + 10) return null;
        const s = typeScale(a.type) * 0.92;
        const col = a.emergency ? G.red : a.lowFuel ? G.amber : a.status === 'dep' ? '#fbbf24' : G.blue;
        return (
          <g key={'air' + a.cs}>
            {planeGlyph('ag' + a.cs, pt.x, pt.y, a.heading, s, col, {
              opacity: 0.92, onClick: () => onSelect && onSelect(a.cs),
              ring: selected === a.cs ? G.green : null, alert: a.emergency,
            })}
            <text x={pt.x + 11} y={pt.y - 6} fill={col} fontSize={9} fontFamily="'Courier New', monospace">{a.cs}</text>
            <text x={pt.x + 11} y={pt.y + 4} fill={G.dim} fontSize={7.5} fontFamily="'Courier New', monospace">{Math.round(a.altitude)}ft</text>
          </g>
        );
      })}

      {/* ground units */}
      {units.map((u) => {
        const s = typeScale(u.type);
        const sel = selected === u.cs;
        const labelCol = u.role === 'dep' ? '#fbbf24' : u.role === 'arr' ? G.green : G.dim;
        return (
          <g key={u.id}>
            {planeGlyph('p' + u.id, u.x, u.y, u.rot, s, u.color, {
              onClick: () => onSelect && onSelect(u.cs),
              ring: sel ? G.green : null,
            })}
            <text x={u.x + 9 * s} y={u.y - 6} fill={labelCol} fontSize={8} fontFamily="'Courier New', monospace">{u.label}</text>
            {u.phase !== 'park' && u.phase !== 'loop' && (
              <text x={u.x + 9 * s} y={u.y + 3} fill={phaseColor[u.phase] || G.dim} fontSize={7}
                fontFamily="'Courier New', monospace">
                {u.phase === 'taxiOut' || u.phase === 'taxiIn' ? 'TAXI'
                  : u.phase === 'hold' ? 'HOLD SHORT' : u.phase === 'roll' ? 'DEPARTING'
                    : u.phase === 'rollout' ? 'LANDING' : ''}
              </text>
            )}
          </g>
        );
      })}

      {/* compass + scale */}
      <g transform={`translate(${DIAG.W - 38},38)`}>
        <circle r={15} fill="#03100b" stroke={G.termEdge} strokeWidth={1} />
        <line x1={0} y1={11} x2={0} y2={-11} stroke={G.green} strokeWidth={1} />
        <path d="M0,-13 L-3,-7 L3,-7 Z" fill={G.green} />
        <text x={0} y={-16} fill={G.green} fontSize={8} fontFamily="'Courier New', monospace" textAnchor="middle">N</text>
      </g>
      <g transform={`translate(20,${DIAG.H - 22})`}>
        <line x1={0} y1={0} x2={DPPM} y2={0} stroke={G.dim} strokeWidth={1} />
        <line x1={0} y1={-3} x2={0} y2={3} stroke={G.dim} strokeWidth={1} />
        <line x1={DPPM} y1={-3} x2={DPPM} y2={3} stroke={G.dim} strokeWidth={1} />
        <text x={DPPM / 2} y={-5} fill={G.dim} fontSize={8} fontFamily="'Courier New', monospace" textAnchor="middle">1 NM</text>
      </g>
    </svg>
  );
}
