import { create } from 'zustand'
import type {
  AirportData,
  Metrics,
  RunSummary,
  ScenarioInfo,
  SimEvent,
  Snapshot,
  Status,
} from './types'

const MAX_EVENTS = 600

interface TimedSnapshot {
  snap: Snapshot
  receivedAt: number
}

interface AtcState {
  airport: AirportData | null
  current: TimedSnapshot | null
  previous: TimedSnapshot | null
  events: SimEvent[]
  metrics: Metrics | null
  status: Status
  scenarios: ScenarioInfo[]
  runs: RunSummary[]
  selected: string | null
  wsConnected: boolean
  lastSavedRun: string | null

  setAirport: (a: AirportData) => void
  applySnapshot: (s: Snapshot) => void
  appendEvents: (evs: SimEvent[]) => void
  setMetrics: (m: Metrics) => void
  setStatus: (s: Status) => void
  setScenarios: (s: ScenarioInfo[]) => void
  setRuns: (r: RunSummary[]) => void
  select: (callsign: string | null) => void
  setWsConnected: (v: boolean) => void
  setLastSavedRun: (id: string) => void
  resetSession: () => void
}

export const useAtcStore = create<AtcState>((set) => ({
  airport: null,
  current: null,
  previous: null,
  events: [],
  metrics: null,
  status: {
    mode: 'idle',
    scenario: null,
    model: null,
    paused: false,
    speed: 8,
    t_s: 0,
  },
  scenarios: [],
  runs: [],
  selected: null,
  wsConnected: false,
  lastSavedRun: null,

  setAirport: (a) => set({ airport: a }),
  applySnapshot: (s) =>
    set((st) => ({
      previous: st.current,
      current: { snap: s, receivedAt: performance.now() },
    })),
  appendEvents: (evs) =>
    set((st) => ({ events: [...st.events, ...evs].slice(-MAX_EVENTS) })),
  setMetrics: (m) => set({ metrics: m }),
  setStatus: (s) => set({ status: s }),
  setScenarios: (s) => set({ scenarios: s }),
  setRuns: (r) => set({ runs: r }),
  select: (callsign) => set({ selected: callsign }),
  setWsConnected: (v) => set({ wsConnected: v }),
  setLastSavedRun: (id) => set({ lastSavedRun: id }),
  resetSession: () =>
    set({ current: null, previous: null, events: [], metrics: null, selected: null }),
}))
