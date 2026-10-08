import {
  ChevronLeftIcon,
  ChevronRightIcon,
  DoubleArrowLeftIcon,
  DoubleArrowRightIcon,
} from '@radix-ui/react-icons'
import { type Table } from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'
import { cn, getPageNumbers } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

type DataTablePaginationProps<TData> = {
  table: Table<TData>
  className?: string
  disabled?: boolean
}

export function DataTablePagination<TData>({
  table,
  className,
  disabled = false,
}: DataTablePaginationProps<TData>) {
  const currentPage = table.getState().pagination.pageIndex + 1
  const totalPages = Math.max(1, table.getPageCount())
  const pageNumbers = getPageNumbers(currentPage, totalPages)
  const { t } = useTranslation()

  return (
    <div
      className={cn(
        'flex min-w-0 flex-col gap-3 px-1 sm:flex-row sm:items-center sm:justify-between',
        className
      )}
    >
      <div className='flex w-full items-center justify-between gap-3 sm:w-auto'>
        <div className='text-sm font-medium text-nowrap sm:hidden'>
          {t('data_table.page_of', { current: currentPage, total: totalPages })}
        </div>
        <div className='flex items-center gap-2'>
          <Select
            disabled={disabled}
            value={`${table.getState().pagination.pageSize}`}
            onValueChange={(value) => {
              table.setPageSize(Number(value))
            }}
          >
            <SelectTrigger
              className='h-8 w-17.5'
              aria-label={t('data_table.rows_per_page')}
            >
              <SelectValue placeholder={table.getState().pagination.pageSize} />
            </SelectTrigger>
            <SelectContent side='top'>
              <SelectGroup>
                {[10, 20, 30, 40, 50].map((pageSize) => (
                  <SelectItem key={pageSize} value={`${pageSize}`}>
                    {pageSize}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          <p className='hidden text-sm font-medium sm:block'>
            {t('data_table.rows_per_page')}
          </p>
        </div>
      </div>

      <div className='flex max-w-full items-center justify-center gap-3 sm:gap-6'>
        <div className='hidden items-center justify-center text-sm font-medium text-nowrap lg:flex'>
          {t('data_table.page_of', { current: currentPage, total: totalPages })}
        </div>
        <div className='flex items-center gap-1 sm:gap-2'>
          <Button
            variant='outline'
            className='hidden size-8 p-0 md:inline-flex'
            onClick={() => table.setPageIndex(0)}
            disabled={disabled || !table.getCanPreviousPage()}
          >
            <span className='sr-only'>Go to first page</span>
            <DoubleArrowLeftIcon className='h-4 w-4' />
          </Button>
          <Button
            variant='outline'
            className='size-8 p-0'
            onClick={() => table.previousPage()}
            disabled={disabled || !table.getCanPreviousPage()}
          >
            <span className='sr-only'>Go to previous page</span>
            <ChevronLeftIcon className='h-4 w-4' />
          </Button>

          {/* Page number buttons */}
          {pageNumbers.map((pageNumber, index) => (
            <div
              key={`${pageNumber}-${index}`}
              className={cn(
                'items-center',
                typeof pageNumber !== 'number' ||
                  Math.abs(pageNumber - currentPage) > 1
                  ? 'hidden sm:flex'
                  : 'flex'
              )}
            >
              {pageNumber === '...' ? (
                <span className='px-1 text-sm text-muted-foreground'>...</span>
              ) : (
                <Button
                  disabled={disabled}
                  variant={currentPage === pageNumber ? 'default' : 'outline'}
                  className='h-8 min-w-8 px-2'
                  onClick={() => table.setPageIndex((pageNumber as number) - 1)}
                >
                  <span className='sr-only'>Go to page {pageNumber}</span>
                  {pageNumber}
                </Button>
              )}
            </div>
          ))}

          <Button
            variant='outline'
            className='size-8 p-0'
            onClick={() => table.nextPage()}
            disabled={disabled || !table.getCanNextPage()}
          >
            <span className='sr-only'>Go to next page</span>
            <ChevronRightIcon className='h-4 w-4' />
          </Button>
          <Button
            variant='outline'
            className='hidden size-8 p-0 md:inline-flex'
            onClick={() => table.setPageIndex(table.getPageCount() - 1)}
            disabled={disabled || !table.getCanNextPage()}
          >
            <span className='sr-only'>Go to last page</span>
            <DoubleArrowRightIcon className='h-4 w-4' />
          </Button>
        </div>
      </div>
    </div>
  )
}
