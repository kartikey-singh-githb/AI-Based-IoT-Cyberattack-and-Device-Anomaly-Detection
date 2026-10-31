import { useState } from 'react'
import { api, ModelInfo } from '../api/client'
import Badge from '../components/Badge'
import { fmt, fmtDateTime, usePolling } from '../hooks/usePolling'

type DatasetMode = 'synthetic' | 'csv' | 'parquet'

const DEFAULT_CSV_PATH = 'synthetic_sample.csv'
const DEFAULT_PARQUET_PATH = 'datasets/ciciot2023_train.parquet'

interface TrainResult {
  status: string
  version: string
  training_rows: number
  feature_count: number
  features: string[]
  contamination: number
  threshold: number
  dataset_name: string
  dataset_kind: string
  metrics: Record<string, any>
  metrics_are_measured: boolean
  message: string
  feature_space: string
  offline: boolean
  artifact_path?: string | null
  artifact_config_path?: string | null
}

export default function ModelPage() {
  const { data, refresh } = usePolling<ModelInfo>(() => api('/model'), 8000)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [dataset, setDataset] = useState<DatasetMode>('synthetic')
  const [datasetPath, setDatasetPath] = useState(DEFAULT_CSV_PATH)
  const [limitRows, setLimitRows] = useState('50000')
  const [result, setResult] = useState<TrainResult | null>(null)

  const train = async () => {
    const fileMode = dataset !== 'synthetic'
    const filePath = datasetPath.trim()
    if (fileMode && !filePath) {
      setNote('Training failed: enter a dataset file path.')
      return
    }

    setBusy(true)
    setNote(null)
    try {
      const res = await api<TrainResult>('/model/train', {
        method: 'POST',
        json: {
          dataset,
          csv_path: dataset === 'csv' ? filePath : null,
          dataset_path: fileMode ? filePath : null,
          label_column: 'label',
          limit_rows: fileMode && limitRows !== '' ? Number(limitRows) : null,
        },
      })
      setResult(res)
      setNote(
        res.offline
          ? `Trained offline dataset model ${res.version} · ${res.message} The live MQTT detector was not changed.`
          : `Trained ${res.version} · ${res.message}`,
      )
      await refresh()
    } catch (e: any) {
      setNote(`Training failed: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  const metrics = data?.metrics || {}
  const resultMetrics = result?.metrics || {}
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
          <h3>Active stream model (live MQTT)</h3>
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
            <select
              value={dataset}
              onChange={(e) => {
                const mode = e.target.value as DatasetMode
                setDataset(mode)
                if (mode === 'csv') setDatasetPath(DEFAULT_CSV_PATH)
                if (mode === 'parquet') setDatasetPath(DEFAULT_PARQUET_PATH)
              }}
            >
              <option value="synthetic">synthetic (stream features)</option>
              <option value="csv">CSV file</option>
              <option value="parquet">Parquet dataset (offline)</option>
            </select>
          </label>
          {dataset !== 'synthetic' ? (
            <>
              <label className="field">{dataset === 'parquet' ? 'Parquet path' : 'CSV path'}
                <input
                  value={datasetPath}
                  onChange={(e) => setDatasetPath(e.target.value)}
                  placeholder={dataset === 'parquet' ? 'D:\\datasets\\ciciot2023_train.parquet' : 'synthetic_sample.csv'}
                  style={{ width: 360 }}
                />
              </label>
              <label className="field">Sample rows
                <input
                  type="number"
                  min={0}
                  value={limitRows}
                  onChange={(e) => setLimitRows(e.target.value)}
                  title="Reproducible random sample; 0 loads every row."
                  style={{ width: 110 }}
                />
              </label>
            </>
          ) : null}
          <button className="primary" onClick={train} disabled={busy}>
            {busy ? 'Training…' : 'Train model'}
          </button>
        </div>
        <p className="muted" style={{ fontSize: 12.2, margin: 0 }}>
          Training uses Isolation Forest on the labelled synthetic sample or a dataset file.
          Metrics are computed on a held-out stratified split and are reported only when
          ground-truth labels exist. Using a method named <span className="mono">label</span>{' '}
          such as in CICIoT2023 is recommended.
        </p>
        {dataset !== 'synthetic' ? (
          <div className="notice warn" style={{ marginTop: 10 }}>
            Dataset-file models are offline only. They are saved under{' '}
            <span className="mono">backend/app/ml/artifacts/offline/</span> and never replace
            the active stream model used for live MQTT detection. The path is read on the
            backend host; the browser does not upload the file. When the API runs on another
            machine, call <span className="mono">POST /model/train</span> directly instead.
          </div>
        ) : null}
      </div>

      {result ? (
        <div className="card" style={{ marginTop: 14 }}>
          <h3>Last training result</h3>
          <dl className="kv">
            <dt>Version</dt><dd className="mono">{result.version}</dd>
            <dt>Feature space</dt><dd><Badge value={result.feature_space} /></dd>
            <dt>Scope</dt>
            <dd>
              {result.offline
                ? 'Offline dataset model — live MQTT detection unchanged'
                : 'Active stream model — serving live MQTT detection'}
            </dd>
            <dt>Dataset</dt><dd>{result.dataset_name} <span className="muted">({result.dataset_kind})</span></dd>
            <dt>Training rows</dt><dd className="mono">{fmt(result.training_rows)}</dd>
            <dt>Features</dt><dd>{result.feature_count}</dd>
            <dt>Contamination</dt><dd className="mono">{result.contamination}</dd>
            <dt>Threshold</dt><dd className="mono">{result.threshold}</dd>
            <dt>Artifact</dt>
            <dd className="mono" style={{ wordBreak: 'break-all' }}>{result.artifact_path || '—'}</dd>
            <dt>Config</dt>
            <dd className="mono" style={{ wordBreak: 'break-all' }}>{result.artifact_config_path || '—'}</dd>
          </dl>
          {result.metrics_are_measured ? (
            <table style={{ marginTop: 10 }}>
              <tbody>
                {([
                  ['accuracy', resultMetrics.accuracy],
                  ['precision', resultMetrics.precision],
                  ['recall', resultMetrics.recall],
                  ['f1', resultMetrics.f1],
                  ['roc_auc', resultMetrics.roc_auc],
                ] as Array<[string, any]>).map(([k, v]) => (
                  <tr key={k}>
                    <td className="muted">{k}</td>
                    <td className="num mono">{typeof v === 'number' ? v.toFixed(4) : '—'}</td>
                  </tr>
                ))}
                {resultMetrics.confusion_matrix ? (
                  <tr>
                    <td className="muted">confusion (tn/fp/fn/tp)</td>
                    <td className="num mono">
                      {resultMetrics.confusion_matrix.tn} / {resultMetrics.confusion_matrix.fp} / {resultMetrics.confusion_matrix.fn} / {resultMetrics.confusion_matrix.tp}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          ) : (
            <p className="muted" style={{ fontSize: 12.2, marginBottom: 0 }}>
              No held-out metrics were produced for this run (a usable label column is
              required).
            </p>
          )}
        </div>
      ) : null}
    </>
  )
}
