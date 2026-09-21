import * as React from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

type PasswordInputProps = Omit<React.ComponentProps<typeof Input>, 'type'> & {
  hidePasswordLabel: string
  showPasswordLabel: string
}

export function PasswordInput({
  className,
  disabled,
  hidePasswordLabel,
  showPasswordLabel,
  ...props
}: PasswordInputProps) {
  const [showPassword, setShowPassword] = React.useState(false)

  return (
    <div className={cn('relative', className)}>
      <Input
        type={showPassword ? 'text' : 'password'}
        className='pe-10'
        disabled={disabled}
        {...props}
      />
      <Button
        type='button'
        size='icon'
        variant='ghost'
        disabled={disabled}
        className='absolute end-1 top-1/2 size-7 -translate-y-1/2 text-muted-foreground'
        onClick={() => setShowPassword((visible) => !visible)}
        aria-label={showPassword ? hidePasswordLabel : showPasswordLabel}
        aria-pressed={showPassword}
      >
        {showPassword ? <EyeOff /> : <Eye />}
      </Button>
    </div>
  )
}
