import {
  normalizeLanguage,
  supportedLanguages,
  type SupportedLanguage,
} from '@/i18n/languages'
import { Check, Languages } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export function LanguageSwitch() {
  const { i18n, t } = useTranslation()
  const currentLanguage = normalizeLanguage(
    i18n.resolvedLanguage || i18n.language
  )

  const changeLanguage = (language: SupportedLanguage) => {
    void i18n.changeLanguage(language)
  }

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button
          variant='ghost'
          size='icon'
          className='rounded-full'
          aria-label={t('common.language')}
        >
          <Languages data-icon='inline-start' />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align='end'>
        <DropdownMenuGroup>
          {supportedLanguages.map((language) => (
            <DropdownMenuItem
              key={language.value}
              onClick={() => changeLanguage(language.value)}
            >
              {language.label}
              {currentLanguage === language.value && (
                <Check className='ms-auto' />
              )}
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
