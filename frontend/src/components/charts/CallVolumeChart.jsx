import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatDate, formatNumber } from '../../lib/format'
import { Legend, TooltipCard, useChartTheme } from './ChartFrame'

/**
 * Call volume over time, split by how each call finished.
 *
 * Stacked bars (not two lines on two axes) - both series are counts on one
 * scale, and the stack height is itself meaningful as total volume.
 */
export default function CallVolumeChart({ data = [], height = 260 }) {
  const theme = useChartTheme()
  const series = [
    { key: 'completed', label: 'Completed', color: theme.series[0] },
    { key: 'failed', label: 'Failed', color: theme.series[1] },
  ]

  const rows = data.map((point) => ({ ...point, label: formatDate(point.bucket) }))

  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -16 }} barCategoryGap="28%">
          <CartesianGrid stroke={theme.grid} vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: theme.muted, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: theme.axis }}
            minTickGap={12}
          />
          <YAxis
            tick={{ fill: theme.muted, fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
            width={48}
          />
          <Tooltip
            cursor={{ fill: theme.grid, opacity: 0.45 }}
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <TooltipCard
                  title={label}
                  rows={[
                    ...payload.map((entry) => ({
                      label: entry.name,
                      value: formatNumber(entry.value),
                      color: entry.color,
                    })),
                    {
                      label: 'Total',
                      value: formatNumber(payload.reduce((sum, entry) => sum + (entry.value || 0), 0)),
                    },
                  ]}
                />
              ) : null
            }
          />
          {series.map((item, index) => (
            <Bar
              key={item.key}
              dataKey={item.key}
              name={item.label}
              stackId="calls"
              fill={item.color}
              // 2px surface gap between stacked segments, rounded data-end on top.
              stroke={theme.surface}
              strokeWidth={2}
              radius={index === series.length - 1 ? [4, 4, 0, 0] : 0}
              maxBarSize={44}
              // The dashboard polls; re-animating on every refetch is noise.
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
      <Legend items={series} />
    </div>
  )
}
