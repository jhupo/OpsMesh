import { Logo } from '@/assets/logo'
import { LanguageSwitch } from '@/components/language-switch'
import { ThemeSwitch } from '@/components/theme-switch'

type AuthLayoutProps = {
  children: React.ReactNode
}

export function AuthLayout({ children }: AuthLayoutProps) {
  return (
    <div className='relative flex min-h-dvh items-center justify-center px-6 py-20'>
      <div className='absolute top-4 right-4 flex gap-1'>
        <ThemeSwitch />
        <LanguageSwitch />
      </div>
      <div className='flex w-full max-w-sm flex-col gap-4'>
        <div className='mb-4 flex items-center justify-center'>
          <Logo className='me-2' />
          <h1 className='text-xl font-medium'>Shadcn Admin</h1>
        </div>
        {children}
      </div>
    </div>
  )
}
