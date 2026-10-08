import { useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Check, ChevronsUpDown, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  capabilityOptions,
  type CapabilityOption,
  type OptionSource,
} from '@/api/capability-options'
import { Button } from '@/components/ui/button'
import {
  Command,
  CommandInput,
  CommandList,
  CommandGroup,
  CommandItem,
  CommandEmpty,
} from '@/components/ui/command'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'

export function ResourcePicker({
  label,
  value,
  onChange,
  source,
  workspaceId,
  local = [],
}: {
  label: string
  value: CapabilityOption | null
  onChange: (value: CapabilityOption | null) => void
  source: OptionSource
  workspaceId?: string
  local?: CapabilityOption[]
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const options = capabilityOptions(source, workspaceId)
  const query = useInfiniteQuery({
    ...options,
    enabled: open && options.enabled,
  })
  const items = [
    ...(query.data?.pages.flatMap((page) => page.items) ?? []),
    ...local,
  ]
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant='outline'
          type='button'
          disabled={!options.enabled}
          role='combobox'
          aria-expanded={open}
          aria-label={label}
          className='w-full justify-between font-normal'
        >
          <span className='truncate'>
            {value?.name || t('capabilityCenter.select')}
          </span>
          <ChevronsUpDown data-icon='inline-end' />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align='start'
        className='w-(--radix-popover-trigger-width) p-0'
      >
        <Command>
          <CommandInput placeholder={t('capabilityCenter.searchOptions')} />
          <CommandList>
            <CommandGroup>
              {query.isFetching && (
                <CommandItem disabled>
                  <Loader2 className='animate-spin motion-reduce:animate-none' />
                  <span className='sr-only'>
                    {t('capabilityCenter.loading')}
                  </span>
                </CommandItem>
              )}
            </CommandGroup>
            <CommandEmpty>
              {query.isError
                ? t('capabilityCenter.loadFailed')
                : t('capabilityCenter.noMatches')}
            </CommandEmpty>
            <CommandGroup>
              <CommandItem
                value='__clear'
                onSelect={() => {
                  onChange(null)
                  setOpen(false)
                }}
              >
                {t('capabilityCenter.none')}
              </CommandItem>
              {items.map((item) => (
                <CommandItem
                  key={item.id}
                  value={`${item.id} ${item.name}`}
                  onSelect={() => {
                    onChange(item)
                    setOpen(false)
                  }}
                >
                  <span className='flex-1 truncate'>{item.name}</span>
                  {item.id === value?.id && <Check />}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
        {query.isError && (
          <Button
            variant='ghost'
            className='w-full'
            onClick={() => void query.refetch()}
          >
            {t('capabilityCenter.retry')}
          </Button>
        )}
        {query.hasNextPage && (
          <Button
            variant='ghost'
            className='w-full'
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {t('capabilityCenter.loadMore')}
          </Button>
        )}
      </PopoverContent>
    </Popover>
  )
}
