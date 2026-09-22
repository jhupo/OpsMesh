import * as React from 'react'
import { type LucideIcon } from 'lucide-react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { cn } from '@/lib/utils'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

export type AnimatedTab = {
  id: string
  label: string
  icon: LucideIcon
  content: React.ReactNode
}

type TabsUnderlineProps = {
  tabs: AnimatedTab[]
  value: string
  onValueChange: (value: string) => void
  className?: string
  panelClassName?: string
}

const transition = {
  type: 'spring' as const,
  stiffness: 340,
  damping: 32,
}

export function TabsUnderline({
  tabs,
  value,
  onValueChange,
  className,
  panelClassName,
}: TabsUnderlineProps) {
  const [hoveredTab, setHoveredTab] = React.useState<string | null>(null)
  const [direction, setDirection] = React.useState(1)
  const prefersReducedMotion = useReducedMotion()
  const instanceId = React.useId()
  const activeTab = tabs.find((tab) => tab.id === value) ?? tabs[0]

  function handleTabChange(nextValue: string) {
    const currentIndex = tabs.findIndex((tab) => tab.id === value)
    const nextIndex = tabs.findIndex((tab) => tab.id === nextValue)
    setDirection(nextIndex >= currentIndex ? 1 : -1)
    onValueChange(nextValue)
  }

  if (!activeTab) return null

  return (
    <Tabs
      value={activeTab.id}
      onValueChange={handleTabChange}
      className={cn('w-full', className)}
    >
      <TabsList
        className='no-scrollbar flex h-auto w-full justify-start gap-0 overflow-x-auto rounded-none border-b bg-transparent p-0'
        onMouseLeave={() => setHoveredTab(null)}
      >
        {tabs.map((tab) => {
          const Icon = tab.icon
          const isActive = activeTab.id === tab.id
          const isHovered = hoveredTab === tab.id

          return (
            <TabsTrigger
              key={tab.id}
              value={tab.id}
              onMouseEnter={() => setHoveredTab(tab.id)}
              className={cn(
                'relative cursor-pointer rounded-none border-0 bg-transparent p-0 text-sm font-medium whitespace-nowrap shadow-none hover:text-foreground data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none dark:data-[state=active]:border-transparent dark:data-[state=active]:bg-transparent',
                isActive ? 'text-foreground' : 'text-muted-foreground'
              )}
            >
              <span className='relative z-10 flex items-center gap-2 rounded-md px-4 py-3'>
                {isHovered && (
                  <motion.span
                    layoutId={`${instanceId}-hover`}
                    className='pointer-events-none absolute inset-0 rounded-md bg-muted/70'
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                  />
                )}
                <Icon className='relative z-10 size-4' aria-hidden='true' />
                <span className='relative z-10'>{tab.label}</span>
              </span>
              {isActive && (
                <motion.span
                  layoutId={`${instanceId}-indicator`}
                  className='absolute right-0 bottom-[-1px] left-0 h-0.5 bg-primary'
                  initial={false}
                  transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                />
              )}
            </TabsTrigger>
          )
        })}
      </TabsList>
      <div className='relative mt-6 overflow-hidden'>
        <AnimatePresence mode='wait' custom={direction} initial={false}>
          <motion.div
            key={activeTab.id}
            custom={direction}
            initial={{
              x: prefersReducedMotion ? 0 : direction > 0 ? 48 : -48,
              opacity: 0,
            }}
            animate={{ x: 0, opacity: 1 }}
            exit={{
              x: prefersReducedMotion ? 0 : direction > 0 ? -48 : 48,
              opacity: 0,
            }}
            transition={prefersReducedMotion ? { duration: 0 } : transition}
            className={cn(
              'min-h-40 rounded-xl border bg-card p-5 sm:p-6',
              panelClassName
            )}
          >
            {activeTab.content}
          </motion.div>
        </AnimatePresence>
      </div>
    </Tabs>
  )
}
