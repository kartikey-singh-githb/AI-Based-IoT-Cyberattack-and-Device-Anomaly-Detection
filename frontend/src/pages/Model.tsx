import { useState } from 'react'
import { api, ModelInfo } from '../api/client'
import Badge from '../components/Badge'
import { fmt, fmtDateTime, usePolling } from '../hooks/usePolling'

export default function ModelPage() {
  const { data, refresh } = usePolling<ModelInfo>(() => api('/model'), 8000)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [dataset, setDataset] = useState<'synthetic' | 'csv'>('synthetic')
  const [csvPath, setCsvPath] = useState('synthetic_sample.csv')

  const train = async () => {
    setBusy(true)
    setNote(null)
    try {
      const res = await api<any>('/model/train', {
        method: 'POST',
        json: { dataset, csv_path: dataset === 'csv' ? csvPath : null, label_column: 'label' },
      })
      setNote(`Trained ${res.version} · ${res.message}`)
      await refresh()
    } catch (e: any) {
      setNote(`Training failed: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  const metrics = data?.metrics || {}
  const metricRows: Array<[string, any]> = [
    ['accuracy', metrics.accuracy],
    ['precision', metrics.precision],
    ['recall', metrics.recall],
    ['f1', metrics.f1],
    ['roc_auc', metrics.roc_auc],
    ['predicted_positive_rate', metrics.predicted_positive_rate],
  ]

  return (
    <>
      <div className="topbar">
        <h1>Model</h1>
        <div className="sub">Isolation Forest anomaly detector</div>
      </div>

      {note ? <div className="notice" style={{ marginBottom: 12 }}>{note}</div> : null}
      {data?.caveat ? <div className="notice warn" style={{ marginBottom: 12 }}>{data.caveat}</div> : null}

      <div className="grid cols-2" style={{ marginBottom: 14 }}>
        <div className="card">
          <h3>Active model</h3>
          {!data?.trained ? (
            <div className="empty">No trained model yet — train one below.</div>
          ) : (
            <dl className="kv">
              <dt>Status</dt><dd><Badge value="online" /> serving</dd>
              <dt>Version</dt><dd className="mono">{data.version}</dd>
              <dt>Algorithm</dt><dd>{data.algorithm}</dd>
              <dt>Feature space</dt><dd><Badge value={data.feature_space} /></dd>
              <dt>Features</dt><dd>{data.feature_count} <span className="muted">({data.features.slice(0, 6).join(', ')}…)</span></dd>
              <dt>Training rows</dt><dd className="mono">{fmt(data.training_rows)}</dd>
              <dt>Trained at</dt><dd className="mono">{fmtDateTime(data.trained_at)}</dd>
              <dt>Contamination</dt><dd className="mono">{data.contamination}</dd>
              <dt>Flag quantile</dt><dd className="mono">{data.flag_quantile ?? '—'}</dd>
              <dt>Threshold</dt><dd className="mono">{data.threshold?.toFixed(6)}</dd>
              <dt>Critical band ≤</dt><dd className="mono">{data.critical_level?.toFixed(6) ?? '—'}</dd>
              <dt>Random seed</dt><dd className="mono">{data.random_seed}</dd>
              <dt>Dataset</dt><dd>{data.dataset_name || '—'} <span className="muted">({data.dataset_kind})</span></dd>
            </dl>
          )}
        </div>

        <div className="card">
          <h3>Held-out evaluation</h3>
          {!data?.metrics_are_measured ? (
            <div className="empty">
              No ground-truth labels available, so no metrics are reported.
              Nothing is estimated or invented.
            </div>
          ) : (
            <>
              <table>
                <tbody>
                  {metricRows.map(([k, v]) => (
                    <tr key={k}>
                      <td className="muted">{k}</td>
                      <td className="num mono">
                        {typeof v === 'number' ? v.toFixed(4) : '—'}
                      </td>
                    </tr>
                  ))}
                  {metrics.confusion_matrix ? (
                    <tr>
                      <td className="muted">confusion (tn/fp/fn/tp)</td>
                      <td className="num mono">
                        {metrics.confusion_matrix.tn} / {metrics.confusion_matrix.fp} / {metrics.confusion_matrix.fn} / {metrics.confusion_matrix.tp}
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
              <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
                {metrics.protocol}
              </p>
            </>
          )}
        </div>
      </div>

      <div className="card">
        <h3>Retrain</h3>
        <div className="row" style={{ marginBottom: 10 }}>
          <label className="field">Dataset
            <select value={dataset} onChange={(e) => setDataset(e.target.value as any)}>
              <option value="synthetic">synthetic (stream features)</option>
              <option value="csv">CSV file</option>
            </select>
          </label>
          {dataset === 'csv' ? (
            <label className="field">CSV path
              <input value={csvPath} onChange={(e) => setCsvPath(e.target.value)} style={{ width: 260 }} />
            </label>
          ) : null}
          <button className="primary" onClick={train} disabled={busy}>
            {busy ? 'Training…' : 'Train model'}
          </button>
        </div>
        <p className="muted" style={{ fontSize: 12.2, margin: 0 }}>
          Training uses Isolation Forest on the labelled synthetic sample (or your CSV).
          Metrics are computed on a held-out stratified split and are reported only when
          ground-truth labels exist.
        </p>
      </div>
    </>
  )
}
