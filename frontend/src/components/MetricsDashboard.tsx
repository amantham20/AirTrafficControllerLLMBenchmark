import { useAtcStore } from '../store'

interface TileProps {
  label: string
  value: string
  sub?: string
  tone?: 'good' | 'warning' | 'critical' | 'neutral'
}

function Tile({ label, value, sub, tone = 'neutral' }: TileProps) {
  return (
    <div className={`tile tile-${tone}`}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  )
}

function scoreTone(score: number): TileProps['tone'] {
  if (score >= 0.8) return 'good'
  if (score >= 0.55) return 'warning'
  return 'critical'
}

const CAUSE_LABELS: Record<string, string> = {
  GATE_HOLD: 'Gate hold',
  TAXI_CONGESTION: 'Taxi congestion',
  RUNWAY_QUEUE: 'Runway queue',
  ATC_INSTRUCTION_ERROR: 'ATC error',
  WEATHER: 'Weather',
}

export default function MetricsDashboard() {
  const metrics = useAtcStore((s) => s.metrics)

  if (!metrics) {
    return (
      <div className="panel metrics">
        <div className="panel-title">Metrics</div>
        <div className="transcript-empty">waiting for data…</div>
      </div>
    )
  }

  const inc = metrics.incidents
  const rej = metrics.rejections
  const incTotal = inc.minor + inc.major + inc.critical
  const rejTotal = rej.minor + rej.major + rej.critical
  const causes = Object.entries(metrics.delay_by_cause_min).filter(
    ([, v]) => v > 0,
  )
  const causeMax = Math.max(1, ...causes.map(([, v]) => v))

  return (
    <div className="panel metrics">
      <div className="panel-title">Metrics</div>
      <div className="tile-grid">
        <Tile
          label="Efficiency score"
          value={metrics.efficiency_score.toFixed(3)}
          tone={scoreTone(metrics.efficiency_score)}
        />
        <Tile
          label="Total delay"
          value={`${metrics.total_delay_min.toFixed(1)}m`}
          sub={`${metrics.go_arounds} go-around${metrics.go_arounds === 1 ? '' : 's'}`}
        />
        <Tile
          label="On-time"
          value={
            metrics.on_time_performance == null
              ? '—'
              : `${Math.round(metrics.on_time_performance * 100)}%`
          }
          sub="D15/A15"
        />
        <Tile
          label="Throughput"
          value={`${metrics.throughput_ops_per_hour.toFixed(1)}/h`}
          sub={`${metrics.completed_takeoffs}↑ ${metrics.completed_landings}↓`}
        />
        <Tile
          label="Taxi ratio"
          value={
            metrics.avg_taxi_ratio == null
              ? '—'
              : `×${metrics.avg_taxi_ratio.toFixed(2)}`
          }
          sub="actual / minimum"
        />
        <Tile
          label="Incidents"
          value={String(incTotal)}
          sub={`${inc.minor} minor · ${inc.major} major · ${inc.critical} critical`}
          tone={inc.critical > 0 ? 'critical' : inc.major > 0 ? 'warning' : incTotal > 0 ? 'neutral' : 'good'}
        />
        <Tile
          label="Rejected instr."
          value={String(rejTotal)}
          sub={`${rej.critical} critical`}
          tone={rej.critical > 0 ? 'critical' : rejTotal > 0 ? 'warning' : 'good'}
        />
        <Tile
          label="LLM latency"
          value={
            metrics.llm_latency_avg_ms == null
              ? '—'
              : `${(metrics.llm_latency_avg_ms / 1000).toFixed(1)}s`
          }
          sub={
            metrics.llm_latency_p95_ms == null
              ? `${metrics.decision_points} decisions`
              : `p95 ${(metrics.llm_latency_p95_ms / 1000).toFixed(1)}s · ${metrics.decision_points} calls`
          }
        />
      </div>

      {causes.length > 0 && (
        <div className="cause-list">
          <div className="cause-title">Delay by cause (min)</div>
          {causes.map(([cause, v]) => (
            <div key={cause} className="cause-row">
              <span className="cause-label">
                {CAUSE_LABELS[cause] ?? cause}
              </span>
              <span className="cause-bar">
                <span
                  className="cause-fill"
                  style={{ width: `${(v / causeMax) * 100}%` }}
                />
              </span>
              <span className="cause-value">{v.toFixed(1)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
