import { useEffect, useMemo, useRef } from 'react'
import { useAtcStore } from '../store'
import type { AircraftSnap, AirportData } from '../types'

// Aircraft color by state — dark-surface categorical slots; identity is
// always carried by the callsign label too, never color alone.
const STATE_COLORS: Record<string, string> = {
  SCHEDULED: '#8a897f',
  PUSHBACK: '#9085e9',
  TAXI_OUT: '#3987e5',
  TAXI_IN: '#3987e5',
  HOLD_SHORT: '#c98500',
  LINE_UP_WAIT: '#d95926',
  TAKEOFF_ROLL: '#d95926',
  LANDING_ROLL: '#d95926',
  ARRIVING: '#199e70',
  DEPARTED: '#199e70',
  AT_GATE: '#8a897f',
}

const PAD = 260

interface XY {
  x: number
  y: number
}

function project(a: AirportData): {
  toSvg: (x: number, y: number) => XY
  viewBox: string
} {
  const xs = a.nodes.map((n) => n.x)
  const ys = a.nodes.map((n) => n.y)
  const minX = Math.min(...xs) - PAD
  const maxX = Math.max(...xs) + PAD
  const minY = Math.min(...ys) - PAD
  const maxY = Math.max(...ys) + PAD
  // World y points north; SVG y points down — flip.
  const toSvg = (x: number, y: number): XY => ({ x: x - minX, y: maxY - y })
  return {
    toSvg,
    viewBox: `0 0 ${maxX - minX} ${maxY - minY}`,
  }
}

function edgeWidth(type: string): number {
  if (type === 'runway') return 46
  if (type === 'taxiway') return 16
  return 12
}

function edgeColor(type: string): string {
  if (type === 'runway') return '#30302d'
  if (type === 'taxiway') return '#3c3c37'
  return '#33332f'
}

interface PlaneProps {
  ac: AircraftSnap
  selected: boolean
  onSelect: (cs: string) => void
}

// Renders at the local origin; the parent group carries the (interpolated)
// translate so the rAF loop can move aircraft without re-rendering React.
function Plane({ ac, selected, onSelect }: PlaneProps) {
  const color = ac.emergency ? '#d03b3b' : STATE_COLORS[ac.state] ?? '#c3c2b7'
  const faded = ac.state === 'AT_GATE' || ac.state === 'SCHEDULED'
  return (
    <g
      onClick={(e) => {
        e.stopPropagation()
        onSelect(ac.callsign)
      }}
      style={{ cursor: 'pointer' }}
      opacity={faded ? 0.55 : 1}
    >
      {ac.emergency && (
        <circle r={46} fill="none" stroke="#d03b3b" strokeWidth={4}>
          <animate
            attributeName="r"
            values="30;56;30"
            dur="1.6s"
            repeatCount="indefinite"
          />
        </circle>
      )}
      {selected && (
        <circle r={40} fill="none" stroke="#ffffff" strokeWidth={2.5} strokeDasharray="6 5" />
      )}
      <g transform={`rotate(${ac.heading_deg})`}>
        <path
          d="M 0 -22 L 14 16 L 0 8 L -14 16 Z"
          fill={color}
          stroke="#111110"
          strokeWidth={2}
        />
      </g>
      <text y={40} textAnchor="middle" className="map-callsign">
        {ac.callsign}
      </text>
      <text y={62} textAnchor="middle" className="map-sublabel">
        {ac.type} {ac.airborne ? `· ${ac.state === 'ARRIVING' ? 'final' : 'climb'}` : ''}
      </text>
    </g>
  )
}

export default function MapView() {
  const airport = useAtcStore((s) => s.airport)
  const current = useAtcStore((s) => s.current)
  const selected = useAtcStore((s) => s.selected)
  const select = useAtcStore((s) => s.select)
  const planesRef = useRef<SVGGElement>(null)

  const proj = useMemo(() => (airport ? project(airport) : null), [airport])
  const nodeIndex = useMemo(() => {
    const idx: Record<string, { x: number; y: number }> = {}
    airport?.nodes.forEach((n) => {
      idx[n.id] = n
    })
    return idx
  }, [airport])

  // Smooth interpolation: each frame, position aircraft between the previous
  // and current snapshot based on wall-clock progress.
  useEffect(() => {
    let raf = 0
    const tick = () => {
      raf = requestAnimationFrame(tick)
      const g = planesRef.current
      const { current: cur, previous: prev } = useAtcStore.getState()
      if (!g || !cur || !proj) return
      const interval = prev
        ? Math.max(1, cur.receivedAt - prev.receivedAt)
        : 1000
      const alpha = Math.min(
        1,
        (performance.now() - cur.receivedAt) / interval,
      )
      const prevBy: Record<string, AircraftSnap> = {}
      prev?.snap.aircraft.forEach((a) => {
        prevBy[a.callsign] = a
      })
      cur.snap.aircraft.forEach((a) => {
        const el = g.querySelector<SVGGElement>(
          `g[data-callsign="${a.callsign}"]`,
        )
        if (!el) return
        const p = prevBy[a.callsign] ?? a
        const x = p.x + (a.x - p.x) * alpha
        const y = p.y + (a.y - p.y) * alpha
        const s = proj.toSvg(x, y)
        el.setAttribute('transform', `translate(${s.x} ${s.y})`)
      })
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [proj])

  if (!airport || !proj) {
    return <div className="map-empty">loading airport…</div>
  }

  const snap = current?.snap
  const closed = new Set(snap?.closed_runways ?? [])
  const activeEnds = new Set(snap?.active_runway_ends ?? [])

  return (
    <svg
      className="map-svg"
      viewBox={proj.viewBox}
      preserveAspectRatio="xMidYMid meet"
      onClick={() => select(null)}
    >
      {/* edges */}
      {airport.edges.map((e) => {
        const a = nodeIndex[e.a]
        const b = nodeIndex[e.b]
        if (!a || !b) return null
        const pa = proj.toSvg(a.x, a.y)
        const pb = proj.toSvg(b.x, b.y)
        const isClosedRunway = e.type === 'runway' && closed.has(e.name)
        return (
          <g key={`${e.a}-${e.b}`}>
            <line
              x1={pa.x}
              y1={pa.y}
              x2={pb.x}
              y2={pb.y}
              stroke={edgeColor(e.type)}
              strokeWidth={edgeWidth(e.type)}
              strokeLinecap="round"
            />
            {e.type === 'runway' && (
              <line
                x1={pa.x}
                y1={pa.y}
                x2={pb.x}
                y2={pb.y}
                stroke={isClosedRunway ? '#d03b3b' : '#8a897f'}
                strokeWidth={2.5}
                strokeDasharray="18 14"
                opacity={0.9}
              />
            )}
          </g>
        )
      })}

      {/* closed runway X marks */}
      {airport.runways
        .filter((rw) => closed.has(rw.id))
        .map((rw) => {
          const first = nodeIndex[rw.nodes[0]]
          const last = nodeIndex[rw.nodes[rw.nodes.length - 1]]
          const mid = proj.toSvg((first.x + last.x) / 2, (first.y + last.y) / 2)
          return (
            <g key={rw.id} transform={`translate(${mid.x} ${mid.y})`}>
              <path
                d="M -34 -34 L 34 34 M -34 34 L 34 -34"
                stroke="#d03b3b"
                strokeWidth={10}
              />
            </g>
          )
        })}

      {/* nodes */}
      {airport.nodes.map((n) => {
        const p = proj.toSvg(n.x, n.y)
        if (n.type === 'gate') {
          return (
            <g key={n.id} transform={`translate(${p.x} ${p.y})`}>
              <rect x={-13} y={-13} width={26} height={26} rx={5} fill="#26261f" stroke="#52514e" strokeWidth={2} />
              <text y={-20} textAnchor="middle" className="map-nodelabel">
                {n.id}
              </text>
            </g>
          )
        }
        if (n.type === 'hold_short') {
          return (
            <g key={n.id} transform={`translate(${p.x} ${p.y})`}>
              <circle r={7} fill="#c98500" stroke="#111110" strokeWidth={1.5} />
              <text y={-13} textAnchor="middle" className="map-nodelabel map-hs">
                {n.id.replace('HS_', 'HS ')}
              </text>
            </g>
          )
        }
        if (n.type === 'taxi' || n.type === 'ramp') {
          return (
            <g key={n.id} transform={`translate(${p.x} ${p.y})`}>
              <circle r={5} fill="#52514e" />
              <text y={-11} textAnchor="middle" className="map-nodelabel">
                {n.id}
              </text>
            </g>
          )
        }
        return null
      })}

      {/* runway end labels */}
      {airport.runways.map((rw) =>
        Object.entries(rw.ends).map(([end, nodeId]) => {
          const n = nodeIndex[nodeId]
          if (!n) return null
          const p = proj.toSvg(n.x, n.y)
          const active = activeEnds.has(end)
          return (
            <g key={`${rw.id}-${end}`} transform={`translate(${p.x} ${p.y})`}>
              <rect
                x={-26}
                y={-18}
                width={52}
                height={36}
                rx={6}
                fill={active ? '#0ca30c' : '#26261f'}
                opacity={active ? 0.92 : 0.85}
                stroke="#111110"
              />
              <text y={7} textAnchor="middle" className="map-rwlabel">
                {end}
              </text>
            </g>
          )
        }),
      )}

      {/* aircraft (positions driven by the rAF interpolator) */}
      <g ref={planesRef}>
        {snap?.aircraft.map((ac) => {
          const p = proj.toSvg(ac.x, ac.y)
          return (
            <g
              key={ac.callsign}
              data-callsign={ac.callsign}
              transform={`translate(${p.x} ${p.y})`}
            >
              <Plane
                ac={ac}
                selected={selected === ac.callsign}
                onSelect={select}
              />
            </g>
          )
        })}
      </g>
    </svg>
  )
}
