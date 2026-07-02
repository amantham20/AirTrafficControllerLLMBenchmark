import { useAtcStore } from './store'
import { api } from './api'
import type { WsMessage } from './types'

let socket: WebSocket | null = null
let retryDelay = 500

export function connectWebSocket(): void {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${window.location.host}/ws`
  socket = new WebSocket(url)
  const store = useAtcStore.getState()

  socket.onopen = () => {
    retryDelay = 500
    useAtcStore.getState().setWsConnected(true)
  }

  socket.onmessage = (raw) => {
    let msg: WsMessage
    try {
      msg = JSON.parse(raw.data as string) as WsMessage
    } catch {
      return
    }
    const st = useAtcStore.getState()
    switch (msg.type) {
      case 'snapshot':
        st.applySnapshot(msg.data)
        break
      case 'events':
        st.appendEvents(msg.data)
        break
      case 'metrics':
        st.setMetrics(msg.data)
        break
      case 'status':
        st.setStatus(msg.data)
        break
      case 'run_saved':
        st.setLastSavedRun(msg.data.run_id)
        api.runs().then(st.setRuns).catch(() => undefined)
        break
    }
  }

  socket.onclose = () => {
    store.setWsConnected(false)
    setTimeout(connectWebSocket, retryDelay)
    retryDelay = Math.min(retryDelay * 2, 8000)
  }
  socket.onerror = () => {
    socket?.close()
  }
}
