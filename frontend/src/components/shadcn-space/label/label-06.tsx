'use client'

import { useId, useState, type ComponentProps, type ReactNode } from 'react'
import { UserRound, type LucideIcon } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { cn } from '@/lib/utils'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type FloatingLabelProps = ComponentProps<typeof Input> & {
  label: string
  icon?: LucideIcon
  trailing?: ReactNode
}

// Registry label-06 extended for controlled forms, password controls and errors.
export default function FloatingLabel({
  id: suppliedId,
  label,
  icon: Icon = UserRound,
  trailing,
  value,
  defaultValue,
  onChange,
  onFocus,
  onBlur,
  className,
  ...props
}: FloatingLabelProps) {
  const generatedId = useId()
  const id = suppliedId ?? generatedId
  const [focused, setFocused] = useState(false)
  const [localValue, setLocalValue] = useState(defaultValue ?? '')
  const reducedMotion = useReducedMotion()
  const isFloated = focused || String(value ?? localValue).length > 0
  const invalid =
    props['aria-invalid'] === true || props['aria-invalid'] === 'true'
  return (
    <div
      className={cn('w-full', className)}
      data-invalid={invalid || undefined}
    >
      <div className='flex items-end gap-3 pb-1'>
        <Icon
          aria-hidden
          className={cn(
            'mb-1 size-4 shrink-0 transition-colors motion-reduce:transition-none',
            invalid
              ? 'text-destructive'
              : focused
                ? 'text-primary'
                : 'text-muted-foreground'
          )}
        />
        <div className='relative min-w-0 flex-1'>
          <Input
            {...props}
            id={id}
            value={value}
            defaultValue={defaultValue}
            placeholder=' '
            onChange={(event) => {
              setLocalValue(event.target.value)
              onChange?.(event)
            }}
            onFocus={(event) => {
              setFocused(true)
              onFocus?.(event)
            }}
            onBlur={(event) => {
              setFocused(false)
              onBlur?.(event)
            }}
            className='peer h-auto rounded-none border-none bg-transparent px-0 pt-5 pb-1 text-sm shadow-none focus-visible:ring-0 aria-invalid:ring-0 dark:bg-transparent'
          />
          <Label
            htmlFor={id}
            className={cn(
              'pointer-events-none absolute start-0 cursor-text transition-[top,font-size,color] duration-300 ease-in-out peer-autofill:top-0.5 peer-autofill:text-xs motion-reduce:duration-150',
              isFloated ? 'top-0.5 text-xs' : 'top-5 text-sm',
              invalid
                ? 'text-destructive'
                : isFloated
                  ? 'text-primary'
                  : 'text-muted-foreground'
            )}
          >
            {label}
          </Label>
        </div>
        {trailing}
      </div>
      <div className='relative h-px'>
        <div className='absolute inset-0 bg-border' />
        <motion.div
          className='absolute inset-0 bg-primary'
          initial={false}
          animate={{ scaleX: focused ? 1 : 0, opacity: focused ? 1 : 0 }}
          transition={{
            duration: reducedMotion ? 0.15 : 0.3,
            ease: [0.4, 0, 0.2, 1],
          }}
          style={{ transformOrigin: 'center' }}
        />
        <motion.div
          className='absolute inset-0 bg-destructive'
          initial={false}
          animate={{ scaleX: invalid ? 1 : 0 }}
          transition={{
            duration: reducedMotion ? 0.15 : 0.3,
            ease: [0.4, 0, 0.2, 1],
          }}
          style={{ transformOrigin: 'left' }}
        />
      </div>
    </div>
  )
}
