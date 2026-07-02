import { useMemo } from 'react'
import { useAtcStore } from '../store'

function fmtClock(t: number): string {
  const m = Math.floor(t / 60)
  const s = Math.floor(t % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

const CAUSE_LABELS: Record<string, string> = {
  GATE_HOLD: 'gate hold',
  TAXI_CONGESTION: 'taxi congestion',
  RUNWAY_QUEUE: 'runway queue',
  ATC_INSTRUCTION_ERROR: 'ATC error',
  WEATHER: 'weather',
}

export default function AircraftDetail() {
  const selected = useAtcStore((s) => s.selected)
  const current = useAtcStore((s) => s.current)
  const events = useAtcStore((s) => s.events)
  const select = useAtcStore((s) => s.select)

  const ac = current?.snap.aircraft.find((a) => a.callsign === selected)
  const history = useMemo(
    () =>
      events
        .filter(
          (e) =>
            e.callsign === selected &&
            ['atc_instruction', 'pilot_request', 'pilot_readback',
              'pilot_unable', 'instruction_rejected', 'incident'].includes(
              e.type,
            ),
        )
        .slice(-12),
    [events, selected],
  )

  if (!selected || !ac) return null

  const delays = Object.entries(ac.delay_by_cause).filter(([, v]) => v > 0)

  return (
    <div className="detail-panel">
      <div className="detail-header">
        <span className="detail-callsign">
          {ac.callsign}
          {ac.emergency && <span className="detail-emergency"> ⚠ EMERGENCY</span>}
        </span>
        <button className="detail-close" onClick={() => select(null)}>
          ×
        </button>
      </div>
      <div className="detail-grid">
        <span>type</span>
        <span>
          {ac.type} ({ac.wake})
        </span>
        <span>flight</span>
        <span>
          {ac.kind} · gate {ac.gate} · rwy {ac.runway}
        </span>
        <span>state</span>
        <span>
          {ac.state}
          {ac.pending_request ? ` (requesting ${ac.pending_request})` : ''}
        </span>
        <span>freq</span>
        <span>{ac.frequency}</span>
        <span>sched</span>
        <span>{fmtClock(ac.scheduled_time_s)}</span>
        <span>speed</span>
        <span>{ac.speed_kt.toFixed(0)} kt</span>
        <span>delay</span>
        <span>
          {ac.total_delay_s}s
          {ac.go_arounds > 0 ? ` · ${ac.go_arounds} go-around(s)` : ''}
        </span>
        {ac.min_taxi_time_s != null && (
          <>
            <span>taxi</span>
            <span>
              {ac.actual_taxi_time_s}s vs {ac.min_taxi_time_s}s min
            </span>
          </>
        )}
      </div>
      {delays.length > 0 && (
        <div className="detail-delays">
          {delays.map(([k, v]) => (
            <span key={k} className="delay-chip">
              {CAUSE_LABELS[k] ?? k}: {v}s
            </span>
          ))}
        </div>
      )}
      <div className="detail-history">
        {history.map((e) => (
          <div key={e.seq} className={`ev-row ${e.type === 'instruction_rejected' || e.type === 'incident' ? 'ev-rejected' : ''}`}>
            <span className="ev-time">{fmtClock(e.t_s)}</span>
            <span className="ev-text">{e.text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
