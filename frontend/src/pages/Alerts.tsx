import { useState } from 'react'
import { api, Alert } from '../api/client'
import Badge from '../components/Badge'
import { fmtDateTime, usePolling } from '../hooks/usePolling'

export default function AlertsPage() {
  const [severity, setSeverity] = useState('')
  const [status, setStatus] = useState('')
  const [message, setMessage] = useState<string | null>(null)

  const qs = new URLSearchParams({ limit: '100' })
  if (severity) qs.set('severity', severity)
  if (status) qs.set('status', status)

  const { data, error, refresh } = usePolling<{ items: Alert[]; meta: { total: number } }>(
    () => api(`/alerts?${qs.toString()}`),
    5000,
  )

  const act = async (id: number, action: 'acknowledge' | 'resolve') => {
    setMessage(null)
    try {
      await api(`/alerts/${id}/${action}`, { method: 'POST' })
      await refresh()
      setMessage(`Alert #${id} ${action}d.`)
    } catch (e: any) {
      setMessage(e.message)
    }
  }

  return (
    <>
      <div className="topbar">
        <h1>Security alerts</h1>
        <div className="row">
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">All severities</option>
            <option value="critical">critical</option>
            <option value="high">high</option>
            <option value="medium">medium</option>
            <option value="low">low</option>
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="open">open</option>
            <option value="acknowledged">acknowledged</option>
            <option value="resolved">resolved</option>
          </select>
          {error ? <span className="error">{error}</span> : null}
        </div>
      </div>

      {message ? <div className="notice" style={{ marginBottom: 12 }}>{message}</div> : null}

      <div className="card">
        {!data?.items?.length ? (
          <div className="empty">No alerts yet.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Time</th><th>Alert</th><th>Device</th><th>Severity</th>
                <th>Category</th><th>Origin</th><th>Status</th><th></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((a) => (
                <tr key={a.id}>
                  <td className="mono">{fmtDateTime(a.timestamp)}</td>
                  <td>
                    <div>#{a.id} {a.title}</div>
                    <details className="desc">
                      <summary>Description</summary>
                      <div className="body mono" style={{ whiteSpace: 'pre-wrap' }}>{a.description}</div>
                    </details>
                  </td>
                  <td className="mono">{a.device_id}</td>
                  <td><Badge value={a.severity} /></td>
                  <td className="muted">{a.category}</td>
                  <td><Badge value={a.origin} /></td>
                  <td><Badge value={a.status} /></td>
                  <td>
                    <div className="row">
                      {a.status === 'open' ? (
                        <button onClick={() => act(a.id, 'acknowledge')}>Ack</button>
                      ) : null}
                      {a.status !== 'resolved' ? (
                        <button onClick={() => act(a.id, 'resolve')}>Resolve</button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
