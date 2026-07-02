import { useEffect } from 'react'
import { api } from './api'
import AircraftDetail from './components/AircraftDetail'
import ControlBar from './components/ControlBar'
import MapView from './components/MapView'
import MetricsDashboard from './components/MetricsDashboard'
import TranscriptPanel from './components/TranscriptPanel'
import { useAtcStore } from './store'
import { connectWebSocket } from './ws'

export default function App() {
  const setAirport = useAtcStore((s) => s.setAirport)
  const setScenarios = useAtcStore((s) => s.setScenarios)
  const setRuns = useAtcStore((s) => s.setRuns)
  const current = useAtcStore((s) => s.current)

  useEffect(() => {
    connectWebSocket()
    api.airport('kmbs').then(setAirport).catch(console.error)
    api.scenarios().then(setScenarios).catch(console.error)
    api.runs().then(setRuns).catch(console.error)
  }, [setAirport, setScenarios, setRuns])

  const weather = current?.snap.weather

  return (
    <div className="app">
      <ControlBar />
      <main className="main">
        <section className="map-wrap">
          <MapView />
          {weather && (
            <div className="weather-chip">
              wind {String(Math.round(weather.wind_dir_deg)).padStart(3, '0')}°
              @ {Math.round(weather.wind_kt)} kt · vis{' '}
              {weather.visibility_sm} SM
              {weather.taxi_speed_factor < 1 &&
                ` · taxi ×${weather.taxi_speed_factor}`}
            </div>
          )}
          <AircraftDetail />
        </section>
        <aside className="sidebar">
          <MetricsDashboard />
          <TranscriptPanel />
        </aside>
      </main>
    </div>
  )
}
