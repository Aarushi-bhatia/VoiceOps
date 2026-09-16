import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { formatNumber, titleCase } from '../../lib/format'
import { TooltipCard, useChartTheme } from './ChartFrame'

/**
 * One measure compared across categories - horizontal bars, sorted, so the
 * labels are readable and the ranking is the thing you see first.
 *
 * A single series needs no legend: the panel title names it. `colorFor` lets a
 * caller carry a second meaning (e.g. retryable vs permanent) with a legend
 * supplied alongside.
 */
export default function CategoryBars({ data = [], height = 240, colorFor, valueLabel = 'Calls' }) {
  const theme = useChartTheme()
  const rows = [...data]
    .sort((a, b) => b.value - a.value)
    .map((row) => ({ ...row, label: titleCase(row.name) }))

  return (
    <ResponsiveContainer width="100%" height={Math.max(height, rows.length * 34 + 24)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 40, bottom: 0, left: 8 }} barCategoryGap="26%">
        <CartesianGrid stroke={theme.grid} horizontal={false} />
        <XAxis type="number" hide allowDecimals={false} />
        <YAxis
          type="category"
          dataKey="label"
          tick={{ fill: theme.muted, fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: theme.axis }}
          width={132}
        />
        <Tooltip
          cursor={{ fill: theme.grid, opacity: 0.45 }}
          content={({ active, payload }) =>
            active && payload?.length ? (
              <TooltipCard
                title={payload[0].payload.label}
                rows={[
                  { label: valueLabel, value: formatNumber(payload[0].value), color: payload[0].payload.fill },
                  ...(payload[0].payload.note ? [{ label: 'Retry', value: payload[0].payload.note }] : []),
                ]}
              />
            ) : null
          }
        />
        <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={20} isAnimationActive={false}>
          {rows.map((row) => (
            <Cell key={row.name} fill={colorFor ? colorFor(row, theme) : theme.series[0]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
