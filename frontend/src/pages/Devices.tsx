import { useState } from 'react'
import { api, Device } from '../api/client'
import Badge from '../components/Badge'
import { fmt, fmtTime, usePolling } from '../hooks/usePolling'

export default function DevicesPage() {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const qs = new URLSearchParams({ limit: '200' })
  if (search) qs.set('search', search)
  if (status) qs.set('status', status)

  const { data, error, loading } = usePolling<{ items: Device[]; meta: { total: number } }>(
    () => api(`/devices?${qs.toString()}`),
    4000,
  )

  return (
    <>
      <div className="topbar">
        <h1>Devices</h1>
        <div className="row">
          <input placeholder="Search id / name…" value={search}
            onChange={(e) => setSearch(e.target.value)} style={{ width: 200 }} />
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="online">online</option>
            <option value="degraded">degraded</option>
            <option value="offline">offline</option>
          </select>
          {loading ? <span className="muted">loading…</span> : null}
          {error ? <span className="error">{error}</span> : null}
        </div>
      </div>

      <div className="card">
        {!data?.items?.length ? (
          <div className="empty">No devices registered. Start the simulation to create a fleet.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Device</th><th>Type</th><th>Status</th><th>Location</th>
                <th className="num">Interval</th><th className="num">Messages</th>
                <th className="num">Anomalies</th><th>Last seen</th><th>Simulated</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((d) => (
                <tr key={d.device_id}>
                  <td>
                    <div className="mono">{d.device_id}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{d.name}</div>
                  </td>
                  <td>{d.device_type}</td>
                  <td><Badge value={d.status} /></td>
                  <td className="muted">{d.location || '—'}</td>
                  <td className="num mono">{fmt(d.expected_interval_s, 2)}s</td>
                  <td className="num mono">{fmt(d.message_count)}</td>
                  <td className="num mono">{fmt(d.anomaly_count)}</td>
                  <td className="mono">{fmtTime(d.last_seen)}</td>
                  <td>{d.is_simulated ? <Badge value="scenario" /> : <span className="muted">real</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
