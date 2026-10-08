import { useState } from 'react'
import { Database, Globe2, Mail, ShieldCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Main } from '@/components/layout/main'
import TabsVertical from '@/components/shadcn-space/tabs/tabs-06'
import { PlatformPageHeading, PlatformSectionHeading } from '../page-heading'
import { PlatformMailSettings } from './mail-settings'
import { useManagement } from './state'

type SettingsTab = 'general' | 'security' | 'governance' | 'mail'

export function PlatformSystemSettings() {
  const { t } = useTranslation()
  const { state, store } = useManagement()
  const [tab, setTab] = useState<SettingsTab>('general')
  const [draft, setDraft] = useState<Record<string, string>>(() => ({
    ...state.settings,
  }))
  const set = (key: string, value: string) =>
    setDraft((current) => ({ ...current, [key]: value }))
  const save = () => {
    try {
      store.saveSettings(draft)
      toast.success(t('platformAdmin.ui.saved'))
    } catch {
      toast.error(t('platformAdmin.ui.unableToSave'))
    }
  }

  const items = [
    { id: 'mail', label: t('mail.title'), icon: Mail },
    {
      id: 'general',
      label: t('platformAdmin.systemSettings.general'),
      icon: Globe2,
    },
    {
      id: 'security',
      label: t('platformAdmin.systemSettings.security'),
      icon: ShieldCheck,
    },
    {
      id: 'governance',
      label: t('platformAdmin.systemSettings.governance'),
      icon: Database,
    },
  ]

  return (
    <Main fixed>
      <PlatformPageHeading
        title={t('platformAdmin.navigation.systemConfiguration')}
        actions={
          tab !== 'mail' && (
            <Button size='sm' onClick={save}>
              {t('platformAdmin.ui.save')}
            </Button>
          )
        }
      />
      <TabsVertical
        items={items}
        value={tab}
        onValueChange={(value) => setTab(value as SettingsTab)}
      >
        <div className='h-full min-h-0 max-w-4xl overflow-y-auto pe-1 pb-4'>
          {tab === 'mail' && <PlatformMailSettings />}
          {tab === 'general' && (
            <SettingsSection title={t('platformAdmin.systemSettings.general')}>
              <FieldGroup className='grid gap-5 md:grid-cols-2'>
                <TextSetting
                  id='platform-name'
                  label={t('platformAdmin.systemSettings.platformName')}
                  value={draft.platformName ?? ''}
                  onChange={(value) => set('platformName', value)}
                />
                <Field>
                  <FieldLabel htmlFor='registration'>
                    {t('platformAdmin.systemSettings.registration')}
                  </FieldLabel>
                  <Select
                    value={draft.registration ?? 'closed'}
                    onValueChange={(value) => set('registration', value)}
                  >
                    <SelectTrigger id='registration' className='w-full'>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {['closed', 'invite', 'open'].map((value) => (
                        <SelectItem key={value} value={value}>
                          {t(`platformAdmin.systemSettings.${value}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <Field>
                  <FieldLabel htmlFor='timezone'>
                    {t('platformAdmin.systemSettings.timezone')}
                  </FieldLabel>
                  <Select
                    value={draft.timezone ?? 'Asia/Shanghai'}
                    onValueChange={(value) => set('timezone', value)}
                  >
                    <SelectTrigger id='timezone' className='w-full'>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {['Asia/Shanghai', 'UTC', 'America/New_York'].map(
                        (value) => (
                          <SelectItem key={value} value={value}>
                            {value}
                          </SelectItem>
                        )
                      )}
                    </SelectContent>
                  </Select>
                </Field>
              </FieldGroup>
            </SettingsSection>
          )}
          {tab === 'security' && (
            <SettingsSection title={t('platformAdmin.systemSettings.security')}>
              <FieldGroup className='grid gap-5 md:grid-cols-2'>
                <TextSetting
                  id='session-hours'
                  type='number'
                  label={t('platformAdmin.systemSettings.sessionHours')}
                  value={draft.sessionHours ?? ''}
                  onChange={(value) => set('sessionHours', value)}
                />
                <TextSetting
                  id='password-length'
                  type='number'
                  label={t('platformAdmin.systemSettings.passwordMinLength')}
                  value={draft.passwordMinLength ?? ''}
                  onChange={(value) => set('passwordMinLength', value)}
                />
                <ToggleSetting
                  id='approval-required'
                  label={t('platformAdmin.systemSettings.approvalRequired')}
                  checked={draft.approvalRequired === 'true'}
                  onCheckedChange={(value) =>
                    set('approvalRequired', String(value))
                  }
                />
              </FieldGroup>
            </SettingsSection>
          )}
          {tab === 'governance' && (
            <SettingsSection
              title={t('platformAdmin.systemSettings.governance')}
            >
              <FieldGroup className='grid gap-5 md:grid-cols-2'>
                <TextSetting
                  id='audit-retention'
                  type='number'
                  label={t('platformAdmin.systemSettings.auditRetention')}
                  value={draft.auditRetention ?? ''}
                  onChange={(value) => set('auditRetention', value)}
                />
                <ToggleSetting
                  id='public-publishing'
                  label={t('platformAdmin.systemSettings.allowPublic')}
                  checked={draft.allowPublic === 'true'}
                  onCheckedChange={(value) => set('allowPublic', String(value))}
                />
              </FieldGroup>
            </SettingsSection>
          )}
        </div>
      </TabsVertical>
    </Main>
  )
}

function SettingsSection({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className='min-w-0 p-1'>
      <PlatformSectionHeading title={title} />
      {children}
    </section>
  )
}

function TextSetting({
  id,
  label,
  value,
  type = 'text',
  onChange,
}: {
  id: string
  label: string
  value: string
  type?: 'text' | 'number'
  onChange: (value: string) => void
}) {
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input
        id={id}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  )
}

function ToggleSetting({
  id,
  label,
  checked,
  onCheckedChange,
}: {
  id: string
  label: string
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}) {
  return (
    <Field orientation='horizontal' className='min-h-9 rounded-md border px-3'>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Switch id={id} checked={checked} onCheckedChange={onCheckedChange} />
    </Field>
  )
}
