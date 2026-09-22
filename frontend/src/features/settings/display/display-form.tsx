import { useSuspenseQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { currentUserQueryOptions } from '@/api/auth'
import { Field, FieldLabel } from '@/components/ui/field'
import { Switch } from '@/components/ui/switch'
import { useDebugPreferences } from '../developer-tools'

export function DisplayForm() {
  const { t } = useTranslation()
  const { data: user } = useSuspenseQuery(currentUserQueryOptions())
  const enabled = useDebugPreferences(
    (state) => state.enabledByUser[user.user_id] ?? false
  )
  const setEnabled = useDebugPreferences((state) => state.setEnabled)
  if (!import.meta.env.DEV || user.platform_admin !== true) return null
  return (
    <Field orientation='horizontal'>
      <FieldLabel htmlFor='developer-tools'>
        {t('opsmesh.developerTools')}
      </FieldLabel>
      <Switch
        id='developer-tools'
        checked={enabled}
        onCheckedChange={(value) => setEnabled(user.user_id, value)}
      />
    </Field>
  )
}
