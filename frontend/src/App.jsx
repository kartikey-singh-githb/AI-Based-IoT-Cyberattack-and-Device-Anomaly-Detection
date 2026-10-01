import { useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  Brain,
  ChevronRight,
  Cpu,
  LayoutDashboard,
  Menu,
  Network,
  Search,
  Settings as SettingsIcon,
  Shield,
  ShieldCheck,
  X,
  Zap,
} from "lucide-react";

import Devices from "./pages/Devices";
import Threats from "./pages/Threats";
import Analytics from "./pages/Analytics";
import AIDetection from "./pages/AIDetection";
import Settings from "./pages/Settings";

const navigation = [
  { name: "Dashboard", icon: LayoutDashboard },
  { name: "Devices", icon: Cpu },
  { name: "Threats", icon: AlertTriangle },
  { name: "Analytics", icon: BarChart3 },
  { name: "AI Detection", icon: Brain },
  { name: "Settings", icon: SettingsIcon },
];

function Dashboard() {
  const stats = [
    {
      title: "Total Devices",
      value: "128",
      change: "+12%",
      icon: Cpu,
      status: "positive",
    },
    {
      title: "Active Devices",
      value: "116",
      change: "+8%",
      icon: Activity,
      status: "positive",
    },
    {
      title: "Threats Detected",
      value: "24",
      change: "-18%",
      icon: AlertTriangle,
      status: "positive",
    },
    {
      title: "AI Accuracy",
      value: "96.8%",
      change: "+2.4%",
      icon: Brain,
      status: "positive",
    },
  ];

  const recentThreats = [
    {
      id: "THR-1042",
      device: "CAM-002",
      type: "Port Scan",
      severity: "High",
      time: "2 min ago",
    },
    {
      id: "THR-1041",
      device: "ESP-007",
      type: "Anomalous Traffic",
      severity: "Medium",
      time: "8 min ago",
    },
    {
      id: "THR-1040",
      device: "ROUTER-01",
      type: "Brute Force",
      severity: "Critical",
      time: "15 min ago",
    },
    {
      id: "THR-1039",
      device: "TEMP-004",
      type: "Unknown Device",
      severity: "Low",
      time: "24 min ago",
    },
  ];

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Security Dashboard</h1>
          <p>Monitor your IoT network and detect threats in real time.</p>
        </div>

        <div className="header-actions">
          <button className="icon-button">
            <Bell size={20} />
          </button>
          <button className="primary-button">
            <Brain size={18} />
            Run AI Scan
          </button>
        </div>
      </div>

      <div className="stats-grid">
        {stats.map((stat) => {
          const Icon = stat.icon;

          return (
            <div className="stat-card" key={stat.title}>
              <div className="stat-top">
                <div className="stat-icon">
                  <Icon size={22} />
                </div>
                <span className="stat-change">{stat.change}</span>
              </div>

              <div className="stat-value">{stat.value}</div>
              <div className="stat-title">{stat.title}</div>
            </div>
          );
        })}
      </div>

      <div className="dashboard-grid">
        <div className="card">
          <div className="card-header">
            <div>
              <h2>Network Status</h2>
              <p>Current IoT infrastructure health</p>
            </div>

            <span className="status-badge status-safe">
              <ShieldCheck size={15} />
              Secure
            </span>
          </div>

          <div className="network-status">
            <div className="network-circle">
              <Shield size={44} />
              <strong>96%</strong>
              <span>Health Score</span>
            </div>

            <div className="network-details">
              <div className="detail-row">
                <span>
                  <Network size={17} />
                  Network Connectivity
                </span>
                <strong>98.2%</strong>
              </div>

              <div className="detail-row">
                <span>
                  <Zap size={17} />
                  Devices Online
                </span>
                <strong>116 / 128</strong>
              </div>

              <div className="detail-row">
                <span>
                  <AlertTriangle size={17} />
                  Active Threats
                </span>
                <strong>3</strong>
              </div>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <div>
              <h2>AI Detection</h2>
              <p>Machine learning threat analysis</p>
            </div>

            <Brain size={24} />
          </div>

          <div className="ai-status">
            <div className="ai-status-icon">
              <Activity size={30} />
            </div>

            <div>
              <strong>AI Engine Active</strong>
              <p>Continuously analyzing device behavior</p>
            </div>
          </div>

          <div className="ai-metrics">
            <div>
              <span>Accuracy</span>
              <strong>96.8%</strong>
            </div>

            <div>
              <span>Scans Today</span>
              <strong>1,248</strong>
            </div>

            <div>
              <span>Anomalies</span>
              <strong>24</strong>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div>
            <h2>Recent Threats</h2>
            <p>Latest security events detected by the system</p>
          </div>

          <button className="text-button">
            View all
            <ChevronRight size={16} />
          </button>
        </div>

        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Threat ID</th>
                <th>Device</th>
                <th>Threat Type</th>
                <th>Severity</th>
                <th>Detected</th>
              </tr>
            </thead>

            <tbody>
              {recentThreats.map((threat) => (
                <tr key={threat.id}>
                  <td>
                    <strong>{threat.id}</strong>
                  </td>
                  <td>{threat.device}</td>
                  <td>{threat.type}</td>
                  <td>
                    <span
                      className={`severity-badge severity-${threat.severity.toLowerCase()}`}
                    >
                      {threat.severity}
                    </span>
                  </td>
                  <td>{threat.time}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function App() {
  const [activePage, setActivePage] = useState("Dashboard");
  const [mobileOpen, setMobileOpen] = useState(false);

  const handleNavigation = (page) => {
    setActivePage(page);
    setMobileOpen(false);
  };

  const renderPage = () => {
    switch (activePage) {
      case "Dashboard":
        return <Dashboard />;

      case "Devices":
        return <Devices />;

      case "Threats":
        return <Threats />;

      case "Analytics":
        return <Analytics />;

      case "AI Detection":
        return <AIDetection />;

      case "Settings":
        return <Settings />;

      default:
        return <Dashboard />;
    }
  };

  return (
    <div className="app">
      {mobileOpen && (
        <div
          className="sidebar-overlay"
          onClick={() => setMobileOpen(false)}
        />
      )}

      <aside
        className={`sidebar ${mobileOpen ? "sidebar-open" : ""}`}
      >
        <div className="sidebar-header">
          <div className="brand">
            <div className="brand-icon">
              <Shield size={24} />
            </div>

            <div>
              <h2>IoT Guard</h2>
              <span>AI Security Platform</span>
            </div>
          </div>

          <button
            className="mobile-close"
            onClick={() => setMobileOpen(false)}
          >
            <X size={20} />
          </button>
        </div>

        <nav className="sidebar-nav">
          <div className="nav-label">MAIN MENU</div>

          {navigation.map((item) => {
            const Icon = item.icon;
            const isActive = activePage === item.name;

            return (
              <button
                key={item.name}
                className={`nav-item ${isActive ? "active" : ""}`}
                onClick={() => handleNavigation(item.name)}
              >
                <Icon size={19} />
                <span>{item.name}</span>
              </button>
            );
          })}
        </nav>

        <div className="sidebar-bottom">
          <div className="system-status">
            <div className="status-dot" />
            <div>
              <strong>System Online</strong>
              <span>All services operational</span>
            </div>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <button
            className="mobile-menu"
            onClick={() => setMobileOpen(true)}
          >
            <Menu size={22} />
          </button>

          <div className="topbar-search">
            <Search size={18} />
            <input
              type="text"
              placeholder="Search devices, threats..."
            />
          </div>

          <div className="topbar-right">
            <div className="connection-status">
              <span className="status-dot" />
              System Online
            </div>

            <div className="user-profile">
              <div className="avatar">KS</div>
              <div className="user-info">
                <strong>Security Admin</strong>
                <span>Administrator</span>
              </div>
            </div>
          </div>
        </header>

        <div className="content">{renderPage()}</div>
      </main>
    </div>
  );
}

export default App;