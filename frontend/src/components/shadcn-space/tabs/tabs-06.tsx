'use client'

import { useId, type ReactNode } from 'react'
import { type LucideIcon } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { cn } from '@/lib/utils'
import { useIsMobile } from '@/hooks/use-mobile'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'

type TabItem = { id: string; label: string; icon: LucideIcon }
type TabsVerticalProps = {
  items: TabItem[]
  value: string
  onValueChange: (value: string) => void
  children: ReactNode
}

// Registry tabs-06: preserve its vertical rail and animated selection; routes own panel content.
export default function TabsVertical({
  items,
  value,
  onValueChange,
  children,
}: TabsVerticalProps) {
  const layoutId = useId()
  const isMobile = useIsMobile()
  const reducedMotion = useReducedMotion()
  const transition = reducedMotion
    ? { duration: 0.15 }
    : { type: 'spring' as const, stiffness: 300, damping: 25 }
  return (
    <Tabs
      value={value}
      onValueChange={onValueChange}
      orientation={isMobile ? 'horizontal' : 'vertical'}
      className='flex w-full flex-col gap-6 md:flex-row'
    >
      <TabsList className='flex h-auto w-full justify-start gap-2 overflow-x-auto rounded-none bg-transparent p-0 pb-2 md:w-44 md:shrink-0 md:flex-col md:justify-start md:overflow-visible md:border-e md:border-border md:pe-4 md:pb-0'>
        {items.map((tab) => {
          const Icon = tab.icon
          const isActive = value === tab.id
          return (
            <TabsTrigger
              key={tab.id}
              value={tab.id}
              className={cn(
                'relative flex h-auto w-auto flex-none cursor-pointer items-center justify-start gap-3 rounded-lg border border-dashed bg-transparent px-4 py-2.5 text-sm font-medium whitespace-nowrap shadow-none transition-colors outline-none after:hidden hover:bg-muted/50 hover:text-foreground data-[state=active]:border-transparent data-[state=active]:bg-transparent data-[state=active]:text-primary data-[state=active]:shadow-none md:w-full dark:data-[state=active]:bg-transparent',
                isActive ? 'text-primary' : 'text-muted-foreground'
              )}
            >
              <Icon className='z-10 size-4' aria-hidden />
              <span className='z-10'>{tab.label}</span>
              {isActive && (
                <motion.div
                  layoutId={layoutId + '-bg'}
                  className='absolute inset-0 rounded-lg bg-primary/10'
                  initial={false}
                  transition={transition}
                />
              )}
              {isActive && (
                <motion.div
                  layoutId={layoutId + '-indicator'}
                  className='absolute start-0 top-1/4 bottom-1/4 hidden w-1 rounded-e-full bg-primary md:block'
                  initial={false}
                  transition={transition}
                />
              )}
            </TabsTrigger>
          )
        })}
      </TabsList>
      <div className='min-w-0 flex-1'>
        {items.map((tab) => (
          <TabsContent
            key={tab.id}
            value={tab.id}
            className='h-full outline-none'
          >
            <motion.div
              initial={{ opacity: 0, x: reducedMotion ? 0 : 10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: reducedMotion ? 0.15 : 0.3 }}
              className='h-full'
            >
              {children}
            </motion.div>
          </TabsContent>
        ))}
      </div>
    </Tabs>
  )
}
