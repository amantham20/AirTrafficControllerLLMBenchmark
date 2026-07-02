import { useState } from 'react'
import { api } from '../api'
import { useAtcStore } from '../store'

const SPEEDS = [1, 4, 8, 20, 60]

function fmtClock(t: number): string {
  const m = Math.floor(t / 60)
  const s = Math.floor(t % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export default function ControlBar() {
  const status = useAtcStore((s) => s.status)
  const scenarios = useAtcStore((s) => s.scenarios)
  const runs = useAtcStore((s) => s.runs)
  const current = useAtcStore((s) => s.current)
  const wsConnected = useAtcStore((s) => s.wsConnected)
  const resetSession = useAtcStore((s) => s.resetSession)

  const [scenario, setScenario] = useState('baseline')
  const [controller, setController] = useState('llm')
  const [model, setModel] = useState('claude-sonnet-4-6')
  const [replayId, setReplayId] = useState('')
  const [error, setError] = useState<string | null>(null)

  const busy = status.mode !== 'idle'

  const guard = (p: Promise<unknown>) =>
    p.then(() => setError(null)).catch((e: Error) => setError(e.message))

  const start = () => {
    resetSession()
    guard(
      api.start({
        scenario,
        controller,
        model: controller === 'llm' ? model : undefined,
        speed: status.speed,
      }),
    )
  }

  const startReplay = () => {
    if (!replayId) return
    resetSession()
    guard(api.replay(replayId, status.speed))
  }

  const inject = (kind: string) => {
    const snap = current?.snap
    if (!snap) return
    if (kind === 'go_around') {
      const target = snap.aircraft.find((a) => a.state === 'ARRIVING')
      if (!target) {
        setError('no arrival on approach to send around')
        return
      }
      guard(api.inject('go_around', { callsign: target.callsign }))
    } else if (kind === 'emergency') {
      const target = snap.aircraft.find(
        (a) => a.state === 'ARRIVING' && !a.emergency,
      )
      if (!target) {
        setError('no eligible arrival for an emergency')
        return
      }
      guard(api.inject('emergency', { callsign: target.callsign }))
    } else if (kind === 'close_0523') {
      guard(api.inject('runway_closure', { runway_id: '05/23' }))
    } else if (kind === 'close_1432') {
      guard(api.inject('runway_closure', { runway_id: '14/32' }))
    } else if (kind === 'reopen') {
      snap.closed_runways.forEach((rw) =>
        guard(api.inject('runway_reopen', { runway_id: rw })),
      )
    } else if (kind === 'wind_shift') {
      guard(
        api.inject('weather_change', { wind_dir_deg: 230, wind_kt: 16 }).then(
          () =>
            api.inject('runway_change', {
              active_ends: ['23'],
              reassign_arrivals: { '05': '23' },
            }),
        ),
      )
    }
  }

  return (
    <header className="controlbar">
      <div className="brand">
        <span className="brand-title">ATC BENCH</span>
        <span className={`ws-dot ${wsConnected ? 'ws-on' : 'ws-off'}`} />
      </div>

      {!busy && (
        <>
          <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
            {scenarios
              .filter((s) => s.id !== 'scripted_demo' || true)
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.id} ({s.aircraft} acft)
                </option>
              ))}
          </select>
          <select
            value={controller}
            onChange={(e) => setController(e.target.value)}
          >
            <option value="llm">LLM controller</option>
            <option value="scripted">scripted</option>
            <option value="null">do nothing</option>
          </select>
          {controller === 'llm' && (
            <input
              className="model-input"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="model id"
            />
          )}
          <button className="btn btn-primary" onClick={start}>
            ▶ start
          </button>
          <span className="divider" />
          <select value={replayId} onChange={(e) => setReplayId(e.target.value)}>
            <option value="">replay stored run…</option>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.scenario} · {r.label} ·{' '}
                {r.efficiency_score?.toFixed(2) ?? '-'} ·{' '}
                {r.created_at.slice(5, 16)}
              </option>
            ))}
          </select>
          <button className="btn" onClick={startReplay} disabled={!replayId}>
            ⏵ replay
          </button>
        </>
      )}

      {busy && (
        <>
          <span className="status-badge">
            {status.mode === 'replay' ? 'REPLAY' : 'LIVE'} ·{' '}
            {status.scenario}
            {status.model ? ` · ${status.model}` : ''}
          </span>
          <span className="sim-clock">{fmtClock(current?.snap.t_s ?? 0)}</span>
          <button
            className="btn"
            onClick={() =>
              guard(api.control(status.paused ? 'play' : 'pause'))
            }
          >
            {status.paused ? '▶ play' : '⏸ pause'}
          </button>
          <span className="speed-group">
            {SPEEDS.map((sp) => (
              <button
                key={sp}
                className={`btn btn-speed ${status.speed === sp ? 'btn-active' : ''}`}
                onClick={() => guard(api.control('speed', sp))}
              >
                {sp}×
              </button>
            ))}
          </span>
          {status.mode === 'live' && (
            <span className="inject-group">
              <span className="inject-label">inject:</span>
              <button className="btn btn-inject" onClick={() => inject('go_around')}>
                go-around
              </button>
              <button className="btn btn-inject" onClick={() => inject('emergency')}>
                emergency
              </button>
              <button className="btn btn-inject" onClick={() => inject('close_0523')}>
                close 05/23
              </button>
              <button className="btn btn-inject" onClick={() => inject('close_1432')}>
                close 14/32
              </button>
              <button className="btn btn-inject" onClick={() => inject('reopen')}>
                reopen
              </button>
              <button className="btn btn-inject" onClick={() => inject('wind_shift')}>
                wind→23
              </button>
            </span>
          )}
          <button className="btn btn-stop" onClick={() => guard(api.control('stop'))}>
            ■ stop
          </button>
        </>
      )}

      {error && <span className="control-error">{error}</span>}
    </header>
  )
}
