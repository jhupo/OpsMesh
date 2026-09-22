import { useTranslation } from 'react-i18next'
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from '@/components/ui/field'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Switch } from '@/components/ui/switch'

// Reserved settings: the backend currently exposes an inbox, not delivery preferences.
// Controls stay disabled rather than pretending to save or alter delivery.
export function NotificationsForm() {
  const { t } = useTranslation()
  const categories = ['taskUpdates', 'approvals', 'security'] as const
  return (
    <FieldGroup className='grid items-stretch gap-4 lg:grid-cols-2'>
      <FieldSet className='h-full gap-2 rounded-lg border p-5'>
        <FieldLegend variant='label'>
          {t('settings_form.notify_about')}
        </FieldLegend>
        <RadioGroup disabled className='gap-1'>
          {(
            ['all_new_messages', 'direct_messages_mentions', 'nothing'] as const
          ).map((key) => (
            <Field
              key={key}
              orientation='horizontal'
              className='min-h-11 border-b px-1 py-2 last:border-b-0'
            >
              <RadioGroupItem id={key} value={key} />
              <FieldLabel htmlFor={key} className='font-normal'>
                {t('settings_form.' + key)}
              </FieldLabel>
            </Field>
          ))}
        </RadioGroup>
      </FieldSet>
      <FieldSet className='h-full gap-2 rounded-lg border p-5'>
        <FieldLegend variant='label'>
          {t('settings_form.email_notifications')}
        </FieldLegend>
        <FieldGroup className='gap-1'>
          {categories.map((key) => (
            <Field
              key={key}
              orientation='horizontal'
              className='min-h-11 border-b px-1 py-2 last:border-b-0'
            >
              <FieldLabel htmlFor={key} className='font-normal'>
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
