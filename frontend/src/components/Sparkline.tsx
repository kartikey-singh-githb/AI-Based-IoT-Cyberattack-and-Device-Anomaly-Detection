/** Tiny inline SVG sparkline (no chart dependency). */
export default function Sparkline({
  values,
  width = 220,
  height = 44,
  color = '#4cc2ff',
}: {
  values: number[]
  width?: number
  height?: number
  color?: string
}) {
  if (!values.length) return <svg className="spark" width={width} height={height} />
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const step = values.length > 1 ? width / (values.length - 1) : width
  const points = values
    .map((v, i) => {
      const x = i * step
      const y = height - 3 - ((v - min) / span) * (height - 6)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  return (
    <svg className="spark" width={width} height={height}>
      <polyline fill="none" stroke={color} strokeWidth="1.6" points={points} />
    </svg>
  )
}
