import { useState } from 'react'
import { api, TelemetryRow } from '../api/client'
import Badge from '../components/Badge'
import { fmtTime, usePolling } from '../hooks/usePolling'

export default function TelemetryPage() {
  const [deviceId, setDeviceId] = useState('')
  const [minutes, setMinutes] = useState('15')
  const qs = new URLSearchParams({ limit: '100' })
  if (deviceId) qs.set('device_id', deviceId)
  if (minutes) qs.set('minutes', minutes)

  const { data, error } = usePolling<{ items: TelemetryRow[]; meta: { total: number } }>(
    () => api(`/telemetry?${qs.toString()}`),
    3000,
  )

  const columns = ['temperature', 'humidity', 'energy_w', 'voltage', 'current_a', 'rssi']

  return (
    <>
      <div className="topbar">
        <h1>Telemetry</h1>
        <div className="row">
          <input placeholder="device_id" value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)} style={{ width: 190 }} />
          <select value={minutes} onChange={(e) => setMinutes(e.target.value)}>
            <option value="5">last 5 min</option>
            <option value="15">last 15 min</option>
            <option value="60">last hour</option>
            <option value="">all</option>
          </select>
          {error ? <span className="error">{error}</span> : null}
        </div>
      </div>

      <div className="card">
        <div className="muted" style={{ marginBottom: 8 }}>
          Showing {data?.items?.length ?? 0} of {data?.meta?.total ?? 0} matching records
        </div>
        {!data?.items?.length ? (
          <div className="empty">No telemetry yet.</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>Time</th><th>Device</th><th>Status</th><th>Source</th>
                  {columns.map((c) => <th key={c} className="num">{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{fmtTime(r.timestamp)}</td>
                    <td className="mono">{r.device_id}</td>
                    <td><Badge value={r.device_status} /></td>
                    <td className="muted">{r.ingest_source}</td>
                    {columns.map((c) => {
                      const v = (r.readings as any)?.[c]
                      const idx = (r as any)[c]
                      const shown = typeof idx === 'number' ? idx : typeof v === 'number' ? v : null
                      return (
                        <td key={c} className="num mono">
                          {shown === null || shown === undefined ? '—' : Number(shown).toFixed(2)}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
