import { LanguageSwitch } from '@/components/language-switch'
import { ThemeSwitch } from '@/components/theme-switch'

type AuthLayoutProps = {
  children: React.ReactNode
}

export function AuthLayout({ children }: AuthLayoutProps) {
  return (
    <main className='auth-surface relative grid min-h-svh overflow-hidden px-6 py-16 sm:px-8'>
      <div className='absolute end-4 top-4 z-10 flex items-center gap-1'>
        <ThemeSwitch />
        <LanguageSwitch />
      </div>
      <div className='relative m-auto flex w-full max-w-md flex-col justify-center'>
        {children}
      </div>
    </main>
  )
}
