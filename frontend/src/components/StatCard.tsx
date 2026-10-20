export default function StatCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: string | number
  hint?: string
  tone?: 'ok' | 'warn' | 'danger' | 'muted'
}) {
  const color =
    tone === 'ok' ? 'var(--ok)'
      : tone === 'warn' ? 'var(--warn)'
        : tone === 'danger' ? 'var(--danger)'
          : tone === 'muted' ? 'var(--muted)'
            : undefined
  return (
    <div className="card stat">
      <span className="label">{label}</span>
      <span className="value" style={color ? { color } : undefined}>{value}</span>
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  )
}
