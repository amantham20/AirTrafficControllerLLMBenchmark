import { useEffect, useRef } from 'react'
import { useAtcStore } from '../store'
import type { SimEvent } from '../types'

function fmtClock(t: number): string {
  const m = Math.floor(t / 60)
  const s = t % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

const HIDDEN_TYPES = new Set(['state_transition'])

function eventClass(ev: SimEvent): string {
  switch (ev.type) {
    case 'atc_instruction':
      return 'ev-atc'
    case 'pilot_request':
    case 'pilot_readback':
      return 'ev-pilot'
    case 'pilot_unable':
      return 'ev-unable'
    case 'instruction_rejected':
      return 'ev-rejected'
    case 'incident':
      return 'ev-incident'
    case 'conflict_alert':
      return 'ev-alert'
    default:
      return 'ev-system'
  }
}

function speakerTag(ev: SimEvent): string {
  switch (ev.type) {
    case 'atc_instruction':
      return 'TWR'
    case 'pilot_request':
    case 'pilot_readback':
    case 'pilot_unable':
      return ev.callsign ?? 'ACFT'
    case 'instruction_rejected':
      return 'REJ'
    case 'incident':
      return '⚠ INC'
    case 'conflict_alert':
      return '⚠ ALR'
    default:
      return 'SYS'
  }
}

export default function TranscriptPanel() {
  const events = useAtcStore((s) => s.events)
  const bottomRef = useRef<HTMLDivElement>(null)
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const box = boxRef.current
    if (!box) return
    const nearBottom =
      box.scrollHeight - box.scrollTop - box.clientHeight < 120
    if (nearBottom) bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [events])

  const visible = events.filter((e) => !HIDDEN_TYPES.has(e.type))

  return (
    <div className="panel transcript">
      <div className="panel-title">Comms transcript</div>
      <div className="transcript-scroll" ref={boxRef}>
        {visible.length === 0 && (
          <div className="transcript-empty">no transmissions yet</div>
        )}
        {visible.map((ev) => (
          <div key={ev.seq} className={`ev-row ${eventClass(ev)}`}>
            <span className="ev-time">{fmtClock(ev.t_s)}</span>
            <span className="ev-speaker">{speakerTag(ev)}</span>
            <span className="ev-text">{ev.text}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
