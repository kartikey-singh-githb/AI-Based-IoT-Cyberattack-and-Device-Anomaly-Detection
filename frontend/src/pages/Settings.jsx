import { Bell, BrainCircuit, Database, Radio, Shield } from "lucide-react";

function Settings() {
  return (
    <main className="dashboard">
      <div className="dashboard-intro">
        <div>
          <span className="eyebrow">SYSTEM CONFIGURATION</span>
          <h2>Settings</h2>
          <p>Configure IoTGuard AI monitoring and detection behavior.</p>
        </div>
      </div>

      <div className="settings-grid">
        <div className="settings-card">
          <div className="settings-heading">
            <BrainCircuit />
            <div>
              <h3>Detection Engine</h3>
              <p>Configure threat detection services.</p>
            </div>
          </div>

          <SettingRow
            title="ML Anomaly Detection"
            description="Detect unusual device behavior"
            enabled
          />

          <SettingRow
            title="Rule-Based Detection"
            description="Identify known attack patterns"
            enabled
          />

          <SettingRow
            title="Real-Time Monitoring"
            description="Continuously monitor device telemetry"
            enabled
          />
        </div>

        <div className="settings-card">
          <div className="settings-heading">
            <Bell />
            <div>
              <h3>Alert Settings</h3>
              <p>Configure security notifications.</p>
            </div>
          </div>

          <SettingRow
            title="Critical Threat Alerts"
            description="Notify when critical threats are detected"
            enabled
          />

          <SettingRow
            title="High Risk Alerts"
            description="Notify for high-risk devices"
            enabled
          />

          <SettingRow
            title="Device Offline Alerts"
            description="Notify when devices disconnect"
            enabled={false}
          />
        </div>

        <div className="settings-card system-info">
          <div className="settings-heading">
            <Shield />
            <div>
              <h3>System Information</h3>
              <p>IoTGuard AI environment.</p>
            </div>
          </div>

          <div className="info-row">
            <span>Version</span>
            <strong>0.1.0</strong>
          </div>

          <div className="info-row">
            <span>Environment</span>
            <strong>Development</strong>
          </div>

          <div className="info-row">
            <span>Backend</span>
            <strong className="backend-offline">Not Connected</strong>
          </div>

          <div className="info-row">
            <span>Database</span>
            <strong className="backend-offline">Not Connected</strong>
          </div>
        </div>
      </div>
    </main>
  );
}

function SettingRow({ title, description, enabled }) {
  return (
    <div className="setting-row">
      <div>
        <strong>{title}</strong>
        <span>{description}</span>
      </div>

      <div className={`toggle ${enabled ? "on" : ""}`}>
        <div />
      </div>
    </div>
  );
}

export default Settings;