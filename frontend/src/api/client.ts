// Thin typed fetch client for the P310 backend.
// Base URL: VITE_API_URL (direct) or the Vite /api proxy in development.

const RAW_BASE = (import.meta.env.VITE_API_URL as string | undefined) || '/api'

export const API_BASE = RAW_BASE.replace(/\/$/, '')

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T = any>(
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<T> {
  const url = `${API_BASE}${path}`
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
  }
  let body = init?.body
  if (init?.json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(init.json)
  }
  const res = await fetch(url, { ...init, headers, body })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = typeof data?.detail === 'string' ? data.detail : JSON.stringify(data)
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

// ---------------------------------------------------------------------------
// Backend payload shapes (subset the dashboard actually renders)
// ---------------------------------------------------------------------------
export interface PageMeta {
  total: number
  limit: number
  offset: number
}

export interface Device {
  device_id: string
  name: string
  device_type: string
  status: string
  location: string
  firmware: string
  is_simulated: boolean
  behaviour_label: string
  message_count: number
  anomaly_count: number
  expected_interval_s: number
  first_seen?: string
  last_seen?: string
  latest_readings?: Record<string, number | string | null>
}

export interface TelemetryRow {
  id: number
  device_id: string
  timestamp: string
  device_status: string
  temperature: number | null
  humidity: number | null
  energy_w: number | null
  voltage: number | null
  current_a: number | null
  rssi: number | null
  readings: Record<string, number | string | null>
  is_synthetic: boolean
  ingest_source: string
}

export interface Anomaly {
  id: number
  device_id: string
  timestamp: string
  score: number | null
  is_anomaly: boolean
  severity: string
  threshold: number
  model_version: string
  description: string
  status: string
  origin: string
  is_synthetic: boolean
  features: Record<string, number>
  deviations: Record<string, number>
  alert_id?: number | null
}

export interface Alert {
  id: number
  anomaly_id?: number | null
  device_id: string
  timestamp: string
  severity: string
  category: string
  title: string
  description: string
  status: string
  origin: string
  is_synthetic: boolean
  acknowledged_at?: string | null
  resolved_at?: string | null
}

export interface Overview {
  total_devices: number
  online_devices: number
  offline_devices: number
  degraded_devices: number
  total_telemetry: number
  telemetry_last_hour: number
  total_anomalies: number
  anomalies_last_hour: number
  open_alerts: number
  critical_alerts: number
  unresolved_alerts: number
  mqtt_connected: boolean
  mqtt_mode: string
  model_trained: boolean
  simulation_running: boolean
  generated_at: string
}

export interface ModelInfo {
  trained: boolean
  algorithm: string
  version: string | null
  trained_at?: string | null
  training_rows: number
  features: string[]
  feature_count: number
  feature_space: string
  contamination: number
  threshold: number
  flag_quantile?: number | null
  critical_level?: number | null
  suspicious_level?: number | null
  random_seed: number
  n_estimators?: number | null
  training_seconds?: number | null
  dataset_name: string
  dataset_kind: string
  dataset_source: string
  metrics: Record<string, any>
  metrics_are_measured: boolean
  notes: string
  artifact_path: string
  artifact_exists: boolean
  caveat: string
}

export interface SimulationStatus {
  simulation: {
    running: boolean
    session_id?: number | null
    device_count: number
    tick_seconds: number
    anomaly_probability: number
    messages_published: number
    anomalies_injected: number
    started_at?: number | null
    transport: string
    devices?: Array<{
      device_id: string
      device_type: string
      next_due_in: number
      burst_remaining: number
      blackout_remaining: number
      pending_scenario?: string | null
    }>
  }
  mqtt: MqttStatus
  scenarios: string[]
}

export interface MqttStatus {
  enabled: boolean
  connected: boolean
  mode: string
  detail: string
  messages_received: number
  messages_sent?: number
  broker: string
  transport?: string
}
