export default function Badge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="badge muted">—</span>
  return <span className={`badge ${String(value).toLowerCase()}`}>{value}</span>
}
