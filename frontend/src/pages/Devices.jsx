import { useState } from "react";
import {
  Search,
  Cpu,
  Wifi,
  WifiOff,
  ShieldAlert,
  X,
} from "lucide-react";

const devices = [
  {
    id: "CAM-002",
    type: "Camera",
    ip: "192.168.1.102",
    status: "Online",
    risk: 91,
    anomaly: 87,
    lastSeen: "Just now",
  },
  {
    id: "ESP-007",
    type: "Temperature Sensor",
    ip: "192.168.1.107",
    status: "Online",
    risk: 67,
    anomaly: 61,
    lastSeen: "12 sec ago",
  },
  {
    id: "ROUTER-01",
    type: "Gateway",
    ip: "192.168.1.1",
    status: "Warning",
    risk: 78,
    anomaly: 74,
    lastSeen: "20 sec ago",
  },
  {
    id: "TEMP-004",
    type: "Environmental Sensor",
    ip: "192.168.1.114",
    status: "Online",
    risk: 42,
    anomaly: 31,
    lastSeen: "35 sec ago",
  },
  {
    id: "ESP-003",
    type: "Motion Sensor",
    ip: "192.168.1.103",
    status: "Offline",
    risk: 21,
    anomaly: 12,
    lastSeen: "8 min ago",
  },
  {
    id: "CAM-004",
    type: "Camera",
    ip: "192.168.1.104",
    status: "Online",
    risk: 18,
    anomaly: 9,
    lastSeen: "2 sec ago",
  },
];

function Devices() {
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);

  const filteredDevices = devices.filter(
    (device) =>
      device.id.toLowerCase().includes(search.toLowerCase()) ||
      device.type.toLowerCase().includes(search.toLowerCase()) ||
      device.ip.includes(search)
  );

  return (
    <main className="dashboard">
      <div className="dashboard-intro">
        <div>
          <span className="eyebrow">DEVICE MANAGEMENT</span>
          <h2>IoT Devices</h2>
          <p>
            Monitor device health, network activity and security risk.
          </p>
        </div>

        <div className="last-updated">
          <span className="status-dot" />
          117 / 128 devices online
        </div>
      </div>

      <div className="page-toolbar">
        <div className="search-box">
          <Search size={17} />
          <input
            placeholder="Search devices..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <div className="filter-buttons">
          <button className="filter-active">All Devices</button>
          <button>Online</button>
          <button>Warning</button>
          <button>Offline</button>
        </div>
      </div>

      <div className="table-card">
        <div className="card-header">
          <div>
            <span className="eyebrow">DEVICE INVENTORY</span>
            <h3>{filteredDevices.length} Devices Found</h3>
          </div>
        </div>

        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Device</th>
                <th>Type</th>
                <th>IP Address</th>
                <th>Status</th>
                <th>Risk Score</th>
                <th>Anomaly Score</th>
                <th>Last Seen</th>
              </tr>
            </thead>

            <tbody>
              {filteredDevices.map((device) => (
                <tr
                  key={device.id}
                  className="clickable-row"
                  onClick={() => setSelected(device)}
                >
                  <td>
                    <div className="device-cell">
                      <div className="device-mini">
                        <Cpu size={15} />
                      </div>
                      <strong>{device.id}</strong>
                    </div>
                  </td>

                  <td>{device.type}</td>

                  <td className="ip-address">{device.ip}</td>

                  <td>
                    <span
                      className={`device-status ${device.status.toLowerCase()}`}
                    >
                      <i />
                      {device.status}
                    </span>
                  </td>

                  <td>
                    <strong
                      className={
                        device.risk >= 80
                          ? "risk-high"
                          : device.risk >= 50
                          ? "risk-medium"
                          : "risk-low"
                      }
                    >
                      {device.risk}
                    </strong>
                  </td>

                  <td>
                    <strong>{device.anomaly}</strong>
                  </td>

                  <td className="time">{device.lastSeen}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {selected && (
        <div className="modal-overlay" onClick={() => setSelected(null)}>
          <div className="device-modal" onClick={(e) => e.stopPropagation()}>
            <button
              className="modal-close"
              onClick={() => setSelected(null)}
            >
              <X size={18} />
            </button>

            <div className="modal-icon">
              <Cpu size={25} />
            </div>

            <span className="eyebrow">DEVICE DETAILS</span>
            <h2>{selected.id}</h2>
            <p>{selected.type}</p>

            <div className="detail-grid">
              <div>
                <span>IP Address</span>
                <strong>{selected.ip}</strong>
              </div>

              <div>
                <span>Status</span>
                <strong>{selected.status}</strong>
              </div>

              <div>
                <span>Risk Score</span>
                <strong className="risk-high">{selected.risk}</strong>
              </div>

              <div>
                <span>Anomaly Score</span>
                <strong>{selected.anomaly}</strong>
              </div>
            </div>

            <div className="ai-warning">
              <ShieldAlert size={18} />
              <div>
                <strong>AI Security Assessment</strong>
                <p>
                  {selected.risk >= 70
                    ? "Abnormal behavior detected. This device requires investigation."
                    : "No significant abnormal behavior detected."}
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

export default Devices;