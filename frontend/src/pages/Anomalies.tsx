import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, Anomaly } from '../api/client'
import Badge from '../components/Badge'
import { fmtTime, usePolling } from '../hooks/usePolling'

export default function AnomaliesPage() {
  const [severity, setSeverity] = useState('')
  const [origin, setOrigin] = useState('')
  const [deviceId, setDeviceId] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)

  const qs = new URLSearchParams({ limit: '100' })
  if (severity) qs.set('severity', severity)
  if (deviceId) qs.set('device_id', deviceId)

  const { data, error } = usePolling<{ items: Anomaly[]; meta: { total: number } }>(
    () => api(`/anomalies?${qs.toString()}`),
    4000,
  )

  const items = (data?.items || []).filter((a) => !origin || a.origin === origin)

  return (
    <>
      <div className="topbar">
        <h1>Anomalies</h1>
        <div className="row">
          <input placeholder="device_id" value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)} style={{ width: 170 }} />
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">All severities</option>
            <option value="suspicious">suspicious</option>
            <option value="critical">critical</option>
          </select>
          <select value={origin} onChange={(e) => setOrigin(e.target.value)}>
            <option value="">All origins</option>
            <option value="detector">model detections</option>
            <option value="scenario">simulated scenarios</option>
          </select>
          {error ? <span className="error">{error}</span> : null}
        </div>
      </div>

      <div className="card">
        <div className="muted" style={{ marginBottom: 8 }}>
          {data?.meta ? `${data.meta.total} matching · showing ${items.length}` : '—'}
        </div>
        {!items.length ? (
          <div className="empty">No anomalies recorded yet.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Time</th><th>Device</th><th>Severity</th><th>Origin</th><th>Status</th>
                <th className="num">Score</th><th className="num">Threshold</th><th>Model</th><th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <>
                  <tr key={a.id}>
                    <td className="mono">{fmtTime(a.timestamp)}</td>
                    <td className="mono">{a.device_id}</td>
                    <td><Badge value={a.severity} /></td>
                    <td><Badge value={a.origin} /></td>
                    <td><Badge value={a.status} /></td>
                    <td className="num mono">{a.score === null ? '—' : a.score.toFixed(4)}</td>
                    <td className="num mono">{a.threshold.toFixed(4)}</td>
                    <td className="mono muted">{a.model_version}</td>
                    <td>
                      <button onClick={() => setExpanded(expanded === a.id ? null : a.id)}>
                        {expanded === a.id ? 'Hide' : 'Details'}
                      </button>
                    </td>
                  </tr>
                  {expanded === a.id ? (
                    <tr key={`${a.id}-detail`}>
                      <td colSpan={9}>
                        <div className="body">
                          <p style={{ marginTop: 0 }}>{a.description}</p>
                          <div className="grid cols-2">
                            <div>
                              <strong>Top deviations (|z|)</strong>
                              {Object.keys(a.deviations).length === 0
                                ? <div className="muted">None recorded.</div>
                                : Object.entries(a.deviations).map(([k, v]) => (
                                  <div key={k} className="mono">{k}: {Number(v).toFixed(3)}</div>
                                ))}
                            </div>
                            <div>
                              <strong>Features</strong>
                              <div className="mono muted" style={{ fontSize: 12 }}>
                                {Object.entries(a.features).slice(0, 24).map(([k, v]) => (
                                  <div key={k}>{k}: {Number(v).toFixed(3)}</div>
                                ))}
                              </div>
                            </div>
                          </div>
                          {a.alert_id ? <p><Link to="/alerts">Alert #{a.alert_id} →</Link></p> : null}
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
