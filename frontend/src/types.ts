export interface AirportNode {
  id: string
  type: 'gate' | 'ramp' | 'taxi' | 'hold_short' | 'runway'
  x: number
  y: number
  hold_short_for?: string | null
}

export interface AirportEdge {
  a: string
  b: string
  type: 'ramp' | 'taxiway' | 'runway'
  name: string
  length_m: number
}

export interface RunwayInfo {
  id: string
  ends: Record<string, string>
  headings: Record<string, number>
  nodes: string[]
  length_m: number
}

export interface AirportData {
  id: string
  name: string
  description: string
  nodes: AirportNode[]
  edges: AirportEdge[]
  runways: RunwayInfo[]
  gates: string[]
}

export interface AircraftSnap {
  callsign: string
  type: string
  wake: string
  kind: 'departure' | 'arrival'
  state: string
  x: number
  y: number
  heading_deg: number
  speed_kt: number
  frequency: string
  gate: string
  runway: string
  scheduled_time_s: number
  emergency: boolean
  pending_request: string | null
  total_delay_s: number
  delay_by_cause: Record<string, number>
  min_taxi_time_s: number | null
  actual_taxi_time_s: number
  go_arounds: number
  airborne: boolean
}

export interface Snapshot {
  t_s: number
  aircraft: AircraftSnap[]
  active_runway_ends: string[]
  closed_runways: string[]
  weather: {
    wind_dir_deg: number
    wind_kt: number
    visibility_sm: number
    taxi_speed_factor: number
    separation_multiplier: number
  }
}

export interface SimEvent {
  seq: number
  t_s: number
  type: string
  callsign?: string | null
  text: string
  severity?: string | null
  incident?: string | null
  data?: Record<string, unknown>
}

export interface Metrics {
  t_s: number
  aircraft_total: number
  completed_takeoffs: number
  completed_landings: number
  total_delay_min: number
  delay_by_cause_min: Record<string, number>
  on_time_performance: number | null
  avg_taxi_ratio: number | null
  throughput_ops_per_hour: number
  incidents: { minor: number; major: number; critical: number }
  rejections: { minor: number; major: number; critical: number }
  weighted_incidents: number
  go_arounds: number
  llm_latency_avg_ms: number | null
  llm_latency_p95_ms: number | null
  decision_points: number
  efficiency_score: number
}

export interface Status {
  mode: 'idle' | 'live' | 'replay'
  scenario: string | null
  model: string | null
  paused: boolean
  speed: number
  t_s: number
}

export interface ScenarioInfo {
  id: string
  name: string
  description: string
  duration_s: number
  aircraft: number
}

export interface RunSummary {
  id: string
  scenario: string
  controller: string
  model: string | null
  label: string
  seed: number
  created_at: string
  completed: boolean
  efficiency_score: number | null
  total_delay_min: number | null
  incidents: { minor: number; major: number; critical: number } | null
  throughput_ops_per_hour: number | null
}

export type WsMessage =
  | { type: 'snapshot'; data: Snapshot }
  | { type: 'events'; data: SimEvent[] }
  | { type: 'metrics'; data: Metrics }
  | { type: 'status'; data: Status }
  | { type: 'run_saved'; data: { run_id: string } }
