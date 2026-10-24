import { AlertTriangle, ShieldCheck, ShieldAlert } from "lucide-react";

const threats = [
  ["THR-1042", "CAM-002", "DDoS", "Critical", 94, "Quarantined"],
  ["THR-1041", "ESP-007", "Port Scan", "High", 81, "Blocked"],
  ["THR-1040", "TEMP-004", "Traffic Anomaly", "Medium", 63, "Monitoring"],
  ["THR-1039", "ROUTER-01", "Brute Force", "High", 78, "Blocked"],
  ["THR-1038", "CAM-001", "Unauthorized Access", "Critical", 89, "Investigating"],
  ["THR-1037", "ESP-003", "Traffic Anomaly", "Low", 38, "Resolved"],
];

function Threats() {
  return (
    <main className="dashboard">
      <div className="dashboard-intro">
        <div>
          <span className="eyebrow">SECURITY OPERATIONS</span>
          <h2>Threat Center</h2>
          <p>
            Investigate detected cyberattacks and abnormal device behavior.
          </p>
        </div>

        <div className="last-updated">
          <span className="status-dot" />
          Monitoring active
        </div>
      </div>

      <div className="threat-summary">
        <div>
          <AlertTriangle />
          <span>Active Threats</span>
          <strong>7</strong>
        </div>

        <div>
          <ShieldAlert />
          <span>Critical</span>
          <strong>2</strong>
        </div>

        <div>
          <ShieldCheck />
          <span>Blocked Today</span>
          <strong>14</strong>
        </div>
      </div>

      <div className="table-card">
        <div className="card-header">
          <div>
            <span className="eyebrow">THREAT LOG</span>
            <h3>Detected Security Events</h3>
          </div>
        </div>

        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Threat ID</th>
                <th>Device</th>
                <th>Attack Type</th>
                <th>Severity</th>
                <th>Risk</th>
                <th>Status</th>
              </tr>
            </thead>

            <tbody>
              {threats.map((threat) => (
                <tr key={threat[0]}>
                  <td className="threat-id">{threat[0]}</td>
                  <td>{threat[1]}</td>
                  <td>{threat[2]}</td>

                  <td>
                    <span
                      className={`severity ${threat[3].toLowerCase()}`}
                    >
                      {threat[3]}
                    </span>
                  </td>

                  <td>
                    <strong className="risk-high">{threat[4]}</strong>
                  </td>

                  <td>
                    <span
                      className={`table-status ${
                        threat[5].toLowerCase()
                      }`}
                    >
                      {threat[5]}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  );
}

export default Threats;