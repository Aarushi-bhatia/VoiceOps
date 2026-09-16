import { Spinner, EmptyState } from './Feedback'

/**
 * Small, dependency-free table. `columns` is [{ key, header, render, align, width }].
 * Rows scroll horizontally inside the panel rather than widening the page.
 */
export default function DataTable({ columns, rows, keyField = 'id', onRowClick, loading, empty, footer }) {
  if (loading) return <Spinner />
  if (!rows?.length) return empty || <EmptyState title="Nothing here yet" />

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr className="border-b text-left" style={{ borderColor: 'var(--border)' }}>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className="px-3 py-2 text-xs font-medium tracking-wide uppercase"
                style={{ color: 'var(--text-muted)', textAlign: column.align || 'left', width: column.width }}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row[keyField]}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              onKeyDown={
                onRowClick
                  ? (event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        onRowClick(row)
                      }
                    }
                  : undefined
              }
              className={`border-b transition-colors focus-ring ${onRowClick ? 'cursor-pointer hover:bg-[var(--surface-sunken)]' : ''}`}
              style={{ borderColor: 'var(--border)' }}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className="px-3 py-2 align-middle"
                  style={{ textAlign: column.align || 'left', color: 'var(--text-secondary)' }}
                >
                  {column.render ? column.render(row) : row[column.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {footer}
    </div>
  )
}
