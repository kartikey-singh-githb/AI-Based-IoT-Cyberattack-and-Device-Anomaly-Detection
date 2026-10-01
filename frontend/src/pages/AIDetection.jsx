import { useState } from "react";
import {
  BrainCircuit,
  CheckCircle2,
  Cpu,
  ShieldAlert,
  Sparkles,
} from "lucide-react";

function AIDetection() {
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState(null);

  const analyze = () => {
    setAnalyzing(true);
    setResult(null);

    setTimeout(() => {
      setAnalyzing(false);
      setResult({
        risk: 87,
        anomaly: 82,
        confidence: 91,
        attack: "Potential DDoS behavior",
      });
    }, 1500);
  };

  return (
    <main className="dashboard">
      <div className="dashboard-intro">
        <div>
          <span className="eyebrow">ARTIFICIAL INTELLIGENCE</span>
          <h2>AI Detection Engine</h2>
          <p>
            Analyze IoT telemetry and identify abnormal security behavior.
          </p>
        </div>

        <div className="ai-active">
          <span className="status-dot" />
          Detection Engine Active
        </div>
      </div>

      <div className="ai-pipeline">
        {[
          "IoT Telemetry",
          "Feature Extraction",
          "Anomaly Detection",
          "Attack Classification",
          "Risk Scoring",
          "AI Explanation",
          "Recommended Action",
        ].map((step, index) => (
          <div className="pipeline-step" key={step}>
            <div className="pipeline-number">{index + 1}</div>
            <span>{step}</span>
          </div>
        ))}
      </div>

      <div className="ai-analysis-grid">
        <div className="chart-card analyzer">
          <div className="ai-header">
            <div className="ai-icon">
              <BrainCircuit size={25} />
            </div>

            <div>
              <span className="eyebrow">AI ANALYZER</span>
              <h3>Run Device Analysis</h3>
            </div>
          </div>

          <p>
            Select a device to simulate the anomaly detection and threat
            classification pipeline.
          </p>

          <select className="device-select">
            <option>CAM-002 — Camera</option>
            <option>ESP-007 — Temperature Sensor</option>
            <option>ROUTER-01 — Gateway</option>
            <option>TEMP-004 — Environmental Sensor</option>
          </select>

          <button className="analyze-button" onClick={analyze}>
            <Sparkles size={17} />
            {analyzing ? "Analyzing telemetry..." : "Run AI Analysis"}
          </button>

          <div className="demo-label">
            DEMO SIMULATION — NOT A REAL-TIME MODEL
          </div>
        </div>

        {result ? (
          <div className="chart-card ai-result">
            <div className="result-header">
              <div>
                <span className="eyebrow">ANALYSIS RESULT</span>
                <h3>Threat Detected</h3>
              </div>

              <ShieldAlert size={22} />
            </div>

            <div className="result-risk">
              <strong>{result.risk}</strong>
              <span>Risk Score</span>
            </div>

            <div className="result-grid">
              <div>
                <span>Anomaly Score</span>
                <strong>{result.anomaly}</strong>
              </div>

              <div>
                <span>Confidence</span>
                <strong>{result.confidence}%</strong>
              </div>
            </div>

            <div className="ai-explanation">
              <CheckCircle2 size={18} />
              <div>
                <strong>{result.attack}</strong>
                <p>
                  Traffic rate is significantly above the device baseline.
                  Multiple abnormal network patterns were detected.
                </p>
              </div>
            </div>

            <div className="recommendation">
              <span>RECOMMENDED ACTION</span>
              <strong>QUARANTINE DEVICE</strong>
            </div>
          </div>
        ) : (
          <div className="chart-card ai-empty">
            <Cpu size={35} />
            <h3>Ready for Analysis</h3>
            <p>
              Run an AI analysis to see the risk score, anomaly score,
              explanation and recommended action.
            </p>
          </div>
        )}
      </div>
    </main>
  );
}

export default AIDetection;