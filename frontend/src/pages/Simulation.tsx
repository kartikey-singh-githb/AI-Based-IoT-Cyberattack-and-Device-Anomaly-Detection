import { useState } from 'react'
import { api, Device, MqttStatus, SimulationStatus } from '../api/client'
import Badge from '../components/Badge'
import { fmt, fmtTime, usePolling } from '../hooks/usePolling'

export default function SimulationPage() {
  const { data, refresh } = usePolling<SimulationStatus>(
    () => api('/simulation/status'),
    2000,
  )
  const { data: devices } = usePolling<{ items: Device[] }>(
    () => api('/devices?limit=200'),
    6000,
  )

  const [deviceCount, setDeviceCount] = useState(6)
  const [deviceId, setDeviceId] = useState('')
  const [scenario, setScenario] = useState('message_flood')
  const [messages, setMessages] = useState(12)
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const sim = data?.simulation
  const mqtt: MqttStatus | undefined = data?.mqtt
  const running = !!sim?.running

  const run = async (fn: () => Promise<any>, ok: (r: any) => string) => {
    setBusy(true)
    setNote(null)
    try {
      const res = await fn()
      setNote(ok(res))
      await refresh()
    } catch (e: any) {
      setNote(`Error: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  const start = () =>
    run(
      () => api('/simulation/start', { method: 'POST', json: { device_count: deviceCount } }),
      (r) => `Simulation started with ${r.device_count} devices (transport ${r.transport}).`,
    )

  const stop = () =>
    run(() => api('/simulation/stop', { method: 'POST' }),
      (r) => `Stopped after ${r.messages_published} messages.`)

  const inject = () =>
    run(
      () => api('/simulation/inject', {
        method: 'POST',
        json: { device_id: deviceId, scenario, messages },
      }),
      (r) => `Injected ${r.scenario} on ${r.device_id}: ${r.messages_queued} messages queued, anomaly #${r.anomaly_id}, alert #${r.alert_id}.`,
    )

  const reconnect = (forceFallback: boolean) =>
    run(
      () => api('/mqtt/reconnect', { method: 'POST', json: { force_fallback: forceFallback } }),
      (r) => `MQTT mode: ${r.mode}.`,
    )

  return (
    <>
      <div className="topbar">
        <h1>Simulation &amp; transport</h1>
        <div className="sub">
          {running
            ? `${fmt(sim?.messages_published)} messages published · ${fmt(sim?.anomalies_injected)} scenarios injected`
            : 'simulator stopped'}
        </div>
      </div>

      {note ? <div className="notice" style={{ marginBottom: 12 }}>{note}</div> : null}

      <div className="grid cols-2" style={{ marginBottom: 14 }}>
        <div className="card">
          <h3>Device simulator</h3>
          <dl className="kv">
            <dt>State</dt><dd><Badge value={running ? 'online' : 'offline'} /> {running ? 'running' : 'stopped'}</dd>
            <dt>Session</dt><dd className="mono">{sim?.session_id ?? '—'}</dd>
            <dt>Devices</dt><dd className="mono">{sim?.device_count ?? 0}</dd>
            <dt>Transport</dt><dd><Badge value={sim?.transport} /></dd>
            <dt>Messages</dt><dd className="mono">{fmt(sim?.messages_published)}</dd>
            <dt>Scenarios</dt><dd className="mono">{fmt(sim?.anomalies_injected)}</dd>
            <dt>Anomaly probability</dt><dd className="mono">{sim?.anomaly_probability}</dd>
          </dl>
          <div className="row" style={{ marginTop: 12 }}>
            {!running ? (
              <>
                <label className="field">Devices
                  <input type="number" min={1} max={32} value={deviceCount}
                    onChange={(e) => setDeviceCount(Number(e.target.value))}
                    style={{ width: 90 }} />
                </label>
                <button className="primary" onClick={start} disabled={busy}>Start simulation</button>
              </>
            ) : (
              <button className="danger" onClick={stop} disabled={busy}>Stop simulation</button>
            )}
          </div>
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
            Virtual devices publish at their profile interval with jitter, plus
            occasional ambient anomalies. Every row is marked synthetic.
          </p>
        </div>

        <div className="card">
          <h3>MQTT transport</h3>
          <dl className="kv">
            <dt>Mode</dt><dd><Badge value={mqtt?.mode} /></dd>
            <dt>Detail</dt><dd>{mqtt?.detail}</dd>
            <dt>Broker</dt><dd className="mono">{mqtt?.broker || '—'}</dd>
            <dt>Transport</dt><dd className="mono">{mqtt?.transport || '—'}</dd>
            <dt>Sent</dt><dd className="mono">{fmt(mqtt?.messages_sent)}</dd>
            <dt>Received</dt><dd className="mono">{fmt(mqtt?.messages_received)}</dd>
          </dl>
          <div className="row" style={{ marginTop: 12 }}>
            <button onClick={() => reconnect(false)} disabled={busy}>Reconnect</button>
            <button onClick={() => reconnect(true)} disabled={busy}>Force fallback</button>
          </div>
          {mqtt?.mode === 'fallback' ? (
            <div className="notice warn" style={{ marginTop: 10 }}>
              No broker was reachable, so messages travel through the labelled
              in-process transport. The pipeline is real; only the broker is
              missing. Start Mosquitto (or <span className="mono">docker compose up mosquitto</span>)
              and reconnect for a genuine MQTT link.
            </div>
          ) : null}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <h3>Inject a scenario</h3>
        <div className="row" style={{ marginBottom: 10 }}>
          <label className="field">Device
            <select value={deviceId} onChange={(e) => setDeviceId(e.target.value)} style={{ width: 250 }}>
              <option value="">— choose —</option>
              {(devices?.items || []).map((d) => (
                <option key={d.device_id} value={d.device_id}>{d.device_id}</option>
              ))}
            </select>
          </label>
          <label className="field">Scenario
            <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
              {(data?.scenarios || []).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <label className="field">Messages
            <input type="number" min={1} max={500} value={messages}
              onChange={(e) => setMessages(Number(e.target.value))} style={{ width: 90 }} />
          </label>
          <button className="primary" onClick={inject} disabled={busy || !deviceId}>
            Inject
          </button>
        </div>
        <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
          Injection does two things: the simulator emits abnormal telemetry (so the
          model can detect it independently), <em>and</em> a clearly labelled
          <span className="mono"> [SIMULATED]</span> event/alert is recorded with
          origin=scenario.
        </p>
      </div>

      <div className="card">
        <h3>Fleet</h3>
        {!sim?.devices?.length ? (
          <div className="empty">No devices in the running simulation.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Device</th><th>Type</th><th className="num">Next tick (s)</th>
                <th className="num">Flood burst</th><th className="num">Blackout</th><th>Pending</th>
              </tr>
            </thead>
            <tbody>
              {sim.devices.map((d) => (
                <tr key={d.device_id}>
                  <td className="mono">{d.device_id}</td>
                  <td>{d.device_type}</td>
                  <td className="num mono">{d.next_due_in}</td>
                  <td className="num mono">{d.burst_remaining}</td>
                  <td className="num mono">{d.blackout_remaining}</td>
                  <td>{d.pending_scenario ? <Badge value={d.pending_scenario} /> : <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
