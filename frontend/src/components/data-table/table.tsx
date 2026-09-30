import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type Table as TableInstance,
} from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

export function ReadOnlyDataTable<TData>({
  data,
  columns,
}: {
  data: TData[]
  columns: ColumnDef<TData>[]
}) {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    autoResetPageIndex: false,
    enableSorting: false,
  })
  return <DataTable table={table} />
}

export function DataTable<TData>({
  table,
  className,
}: {
  table: TableInstance<TData>
  className?: string
}) {
  const { t } = useTranslation()
  const rows = table.getRowModel().rows
  return (
    <div className='min-w-0'>
      <Table className={className} aria-label={t('data_table.table')}>
        <TableHeader className='sticky top-0 z-20 bg-background/95 backdrop-blur'>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id} className='group/row'>
              {headerGroup.headers.map((header) => (
                <TableHead
                  key={header.id}
                  colSpan={header.colSpan}
                  className={cn(
                    'sticky top-0 !z-20 h-11 bg-background/95 font-semibold backdrop-blur group-hover/row:bg-muted group-data-[state=selected]/row:bg-muted',
                    header.column.columnDef.meta?.className,
                    header.column.columnDef.meta?.thClassName
                  )}
                >
                  {header.isPlaceholder
                    ? null
                    : flexRender(
                        header.column.columnDef.header,
                        header.getContext()
                      )}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow
              key={row.id}
              data-state={row.getIsSelected() && 'selected'}
              className='group/row'
            >
              {row.getVisibleCells().map((cell) => (
                <TableCell
                  key={cell.id}
                  className={cn(
                    'h-14 bg-background group-hover/row:bg-muted group-data-[state=selected]/row:bg-muted',
                    cell.column.columnDef.meta?.className,
                    cell.column.columnDef.meta?.tdClassName
                  )}
                >
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {rows.length === 0 && (
        <div
          role='status'
          className='flex h-24 items-center justify-center text-sm'
        >
          {t('data_table.no_results')}
        </div>
      )}
    </div>
  )
}
