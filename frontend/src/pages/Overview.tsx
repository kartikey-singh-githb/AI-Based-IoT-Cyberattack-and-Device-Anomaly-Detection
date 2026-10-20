import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { api, Anomaly, Overview as OverviewT } from '../api/client'
import Badge from '../components/Badge'
import StatCard from '../components/StatCard'
import { fmt, fmtTime, usePolling } from '../hooks/usePolling'

function Bar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0
  return (
    <div className="bar-row">
      <span className="muted">{label}</span>
      <div className="bar-track">
        <div className="bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="num">{value}</span>
    </div>
  )
}

export default function OverviewPage() {
  const { data: ov, error } = usePolling<OverviewT>(
    () => api('/analytics/overview'),
    3000,
  )
  const { data: anomalies } = usePolling<{ items: Anomaly[]; meta: { total: number } }>(
    () => api('/anomalies?limit=25'),
    4000,
  )

  const severityCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const a of anomalies?.items || []) {
      counts[a.severity] = (counts[a.severity] || 0) + 1
    }
    return counts
  }, [anomalies])

  const maxSeverity = Math.max(1, ...Object.values(severityCounts))

  return (
    <>
      <div className="topbar">
        <h1>Overview</h1>
        <div className="sub">
          {ov ? <>Generated {fmtTime(ov.generated_at)}</> : null}
          {error ? <span className="error"> · {error}</span> : null}
        </div>
      </div>

      <div className="grid cols-4" style={{ marginBottom: 14 }}>
        <StatCard label="Devices" value={ov ? ov.total_devices : '—'}
          hint={ov ? `${ov.online_devices} online · ${ov.degraded_devices} degraded` : undefined} />
        <StatCard label="Telemetry rows" value={ov ? fmt(ov.total_telemetry) : '—'}
          hint={ov ? `${fmt(ov.telemetry_last_hour)} in the last hour` : undefined} />
        <StatCard label="Anomalies" value={ov ? fmt(ov.total_anomalies) : '—'}
          hint={ov ? `${fmt(ov.anomalies_last_hour)} in the last hour` : undefined}
          tone={ov && ov.total_anomalies > 0 ? 'warn' : undefined} />
        <StatCard label="Open alerts" value={ov ? fmt(ov.open_alerts) : '—'}
          hint={ov ? `${ov.critical_alerts} critical` : undefined}
          tone={ov && ov.critical_alerts > 0 ? 'danger' : undefined} />
      </div>

      <div className="grid cols-3" style={{ marginBottom: 14 }}>
        <div className="card">
          <h3>Pipeline status</h3>
          <dl className="kv">
            <dt>MQTT transport</dt>
            <dd>
              <Badge value={ov?.mqtt_mode} />{' '}
              <span className="muted">
                {ov?.mqtt_mode === 'connected'
                  ? 'real broker link'
                  : ov?.mqtt_mode === 'fallback'
                    ? 'labelled in-process fallback (no broker)'
                    : 'not connected'}
              </span>
            </dd>
            <dt>Simulation</dt>
            <dd><Badge value={ov?.simulation_running ? 'online' : 'offline'} /> {ov?.simulation_running ? 'running' : 'stopped'}</dd>
            <dt>Model</dt>
            <dd>
              {ov?.model_trained
                ? <><Badge value="online" /> trained and serving</>
                : <><Badge value="offline" /> <Link to="/model">train one</Link></>}
            </dd>
          </dl>
        </div>

        <div className="card">
          <h3>Recent anomaly severities</h3>
          {Object.keys(severityCounts).length === 0 ? (
            <div className="empty">No anomalies recorded yet.</div>
          ) : (
            <>
              <Bar label="suspicious" value={severityCounts.suspicious || 0} max={maxSeverity} color="var(--medium)" />
              <Bar label="critical" value={severityCounts.critical || 0} max={maxSeverity} color="var(--critical)" />
              <Bar label="other" value={(severityCounts.normal || 0) + (severityCounts.high || 0)} max={maxSeverity} color="var(--accent)" />
            </>
          )}
        </div>

        <div className="card">
          <h3>Interpretation</h3>
          <p className="muted" style={{ margin: 0, fontSize: 12.8 }}>
            The Isolation Forest scores each message against a baseline learned
            from normal traffic. A detection means <em>statistically unusual</em>
            behaviour relative to that baseline — it is not, by itself, proof of
            an attack. Alerts labelled <Badge value="scenario" /> come from the
            simulator and are explicitly simulated.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="row between" style={{ marginBottom: 8 }}>
          <h3 style={{ margin: 0 }}>Latest anomalies</h3>
          <Link to="/anomalies">View all →</Link>
        </div>
        {!anomalies?.items?.length ? (
          <div className="empty">Nothing yet — start the simulation to generate traffic.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Time</th><th>Device</th><th>Severity</th><th>Origin</th>
                <th className="num">Score</th><th className="num">Threshold</th>
              </tr>
            </thead>
            <tbody>
              {anomalies.items.slice(0, 12).map((a) => (
                <tr key={a.id}>
                  <td className="mono">{fmtTime(a.timestamp)}</td>
                  <td className="mono">{a.device_id}</td>
                  <td><Badge value={a.severity} /></td>
                  <td><Badge value={a.origin} /></td>
                  <td className="num mono">{a.score === null ? '—' : a.score.toFixed(4)}</td>
                  <td className="num mono">{a.threshold.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
