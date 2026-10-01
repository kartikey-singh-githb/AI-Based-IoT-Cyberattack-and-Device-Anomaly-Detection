import { Activity, Shield, Target, TrendingUp } from "lucide-react";

function Analytics() {
  return (
    <main className="dashboard">
      <div className="dashboard-intro">
        <div>
          <span className="eyebrow">SECURITY INTELLIGENCE</span>
          <h2>Analytics</h2>
          <p>Analyze attack patterns and IoT security behavior.</p>
        </div>
      </div>

      <div className="analytics-stats">
        <div className="analytics-stat">
          <Target />
          <span>Detection Accuracy</span>
          <strong>94.2%</strong>
          <small>DEMO METRIC</small>
        </div>

        <div className="analytics-stat">
          <Activity />
          <span>Anomalies Detected</span>
          <strong>247</strong>
          <small>LAST 30 DAYS</small>
        </div>

        <div className="analytics-stat">
          <Shield />
          <span>Threats Blocked</span>
          <strong>186</strong>
          <small>LAST 30 DAYS</small>
        </div>

        <div className="analytics-stat">
          <TrendingUp />
          <span>Network Health</span>
          <strong>94%</strong>
          <small>CURRENT</small>
        </div>
      </div>

      <div className="analytics-grid">
        <div className="chart-card">
          <div className="card-header">
            <div>
              <span className="eyebrow">ATTACK ANALYSIS</span>
              <h3>Attacks Over Time</h3>
            </div>
          </div>

          <div className="fake-bars">
            {[45, 62, 38, 74, 53, 86, 66, 91, 57, 72, 44, 68].map(
              (height, index) => (
                <div key={index} className="bar-column">
                  <div
                    className="bar"
                    style={{ height: `${height}%` }}
                  />
                  <span>{index + 1}</span>
                </div>
              )
            )}
          </div>
        </div>

        <div className="chart-card">
          <div className="card-header">
            <div>
              <span className="eyebrow">ATTACK TYPES</span>
              <h3>Distribution</h3>
            </div>
          </div>

          <div className="attack-distribution">
            <div className="distribution-circle">
              <strong>247</strong>
              <span>Events</span>
            </div>

            <div className="distribution-list">
              <span>● DDoS <strong>32%</strong></span>
              <span>● Port Scan <strong>25%</strong></span>
              <span>● Brute Force <strong>18%</strong></span>
              <span>● Anomaly <strong>15%</strong></span>
              <span>● Other <strong>10%</strong></span>
            </div>
          </div>
        </div>
      </div>

      <div className="demo-note">
        <strong>Demo data:</strong> Analytics metrics are simulated for the
        frontend demonstration and will be replaced with actual model results
        after backend integration.
      </div>
    </main>
  );
}

export default Analytics;