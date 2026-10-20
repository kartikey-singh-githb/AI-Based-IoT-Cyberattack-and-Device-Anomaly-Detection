import { NavLink, Outlet } from 'react-router-dom'

const LINKS = [
  { to: '/', label: 'Overview', end: true },
  { to: '/devices', label: 'Devices' },
  { to: '/telemetry', label: 'Telemetry' },
  { to: '/anomalies', label: 'Anomalies' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/model', label: 'Model' },
  { to: '/simulation', label: 'Simulation' },
]

export default function Layout() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          P310 · Anomaly Detection
          <small>AI-based IoT cyberattack detection</small>
        </div>
        <nav className="nav">
          {LINKS.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.end}
              className={({ isActive }) => (isActive ? 'active' : '')}>
              {l.label}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          All telemetry shown here is <strong>simulated</strong>. Detections are
          statistical anomalies, not confirmed attacks.
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
