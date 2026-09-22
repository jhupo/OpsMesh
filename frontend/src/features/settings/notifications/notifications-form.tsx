import { useTranslation } from 'react-i18next'
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from '@/components/ui/field'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Separator } from '@/components/ui/separator'
import { Switch } from '@/components/ui/switch'

// Reserved settings: the backend currently exposes an inbox, not delivery preferences.
// Controls stay disabled rather than pretending to save or alter delivery.
export function NotificationsForm() {
  const { t } = useTranslation()
  const categories = ['taskUpdates', 'approvals', 'security'] as const
  return (
    <FieldGroup className='gap-8'>
      <FieldSet>
        <FieldLegend variant='label'>
          {t('settings_form.notify_about')}
        </FieldLegend>
        <RadioGroup disabled className='grid gap-4 sm:grid-cols-3'>
          {(
            ['all_new_messages', 'direct_messages_mentions', 'nothing'] as const
          ).map((key) => (
            <Field key={key} orientation='horizontal' data-disabled>
              <RadioGroupItem id={key} value={key} />
              <FieldLabel htmlFor={key}>{t('settings_form.' + key)}</FieldLabel>
            </Field>
          ))}
        </RadioGroup>
      </FieldSet>
      <Separator />
      <FieldSet>
        <FieldLegend variant='label'>
          {t('settings_form.email_notifications')}
        </FieldLegend>
        <FieldGroup className='grid gap-6 sm:grid-cols-2'>
          {categories.map((key) => (
            <Field key={key} orientation='horizontal' data-disabled>
              <FieldLabel htmlFor={key}>
                {t('opsmesh.notificationPreferences.' + key)}
              </FieldLabel>
              <Switch id={key} disabled checked={false} />
            </Field>
          ))}
        </FieldGroup>
      </FieldSet>
    </FieldGroup>
  )
}
