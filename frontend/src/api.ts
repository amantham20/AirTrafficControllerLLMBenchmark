import type { AirportData, RunSummary, ScenarioInfo, Status } from './types'

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status}: ${body}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  airport: (id: string) =>
    fetch(`/api/airport/${id}`).then((r) => json<AirportData>(r)),
  scenarios: () => fetch('/api/scenarios').then((r) => json<ScenarioInfo[]>(r)),
  runs: () => fetch('/api/runs').then((r) => json<RunSummary[]>(r)),
  status: () => fetch('/api/status').then((r) => json<Status>(r)),
  start: (body: {
    scenario: string
    controller: string
    model?: string
    speed?: number
  }) =>
    fetch('/api/session/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => json<Status>(r)),
  replay: (runId: string, speed: number) =>
    fetch('/api/session/replay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: runId, speed }),
    }).then((r) => json<Status>(r)),
  control: (action: string, speed?: number) =>
    fetch('/api/session/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, speed }),
    }).then((r) => json<Status>(r)),
  inject: (kind: string, params: Record<string, unknown>, announce = '') =>
    fetch('/api/session/inject', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, params, announce }),
    }).then((r) => json<{ queued: string }>(r)),
}
