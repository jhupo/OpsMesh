import { Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldSet,
  FieldLegend,
} from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { type Parameter } from './drafts'

export function ParameterFields({
  label,
  value,
  onChange,
}: {
  label: string
  value: Parameter[]
  onChange: (value: Parameter[]) => void
}) {
  const { t } = useTranslation()
  const tr = (key: string) => t(`capabilityCenter.${key}`)
  const update = (id: string, patch: Partial<Parameter>) =>
    onChange(
      value.map((item) => (item.id === id ? { ...item, ...patch } : item))
    )
  return (
    <FieldSet>
      <FieldLegend className='flex w-full items-center justify-between text-sm'>
        {label}
        <Button
          type='button'
          variant='ghost'
          size='sm'
          disabled={value.length >= 32}
          onClick={() =>
            onChange([
              ...value,
              {
                id: crypto.randomUUID(),
                name: '',
                type: 'string',
                required: false,
                description: '',
              },
            ])
          }
        >
          <Plus data-icon='inline-start' />
          {tr('addParameter')}
        </Button>
      </FieldLegend>
      <FieldGroup className='gap-3'>
        {value.map((item) => (
          <FieldGroup key={item.id} className='gap-3 rounded-lg border p-3'>
            <div className='flex flex-wrap items-end gap-2'>
              <Field className='min-w-32 flex-1'>
                <FieldLabel htmlFor={`param-${item.id}`}>
                  {tr('parameterName')}
                </FieldLabel>
                <Input
                  id={`param-${item.id}`}
                  autoComplete='off'
                  spellCheck={false}
                  value={item.name}
                  maxLength={80}
                  onChange={(event) =>
                    update(item.id, { name: event.target.value })
                  }
                />
              </Field>
              <Field className='w-32'>
                <FieldLabel>{tr('type')}</FieldLabel>
                <Select
                  value={item.type}
                  onValueChange={(type) =>
                    update(item.id, { type: type as Parameter['type'] })
                  }
                >
                  <SelectTrigger
                    aria-label={`${label} ${tr('type')}`}
                    className='w-full'
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {['string', 'number', 'integer', 'boolean'].map(
                        (type) => (
                          <SelectItem key={type} value={type}>
                            {tr(type)}
                          </SelectItem>
                        )
                      )}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              <Button
                type='button'
                variant='ghost'
                size='icon'
                aria-label={tr('removeParameter')}
                onClick={() =>
                  onChange(
                    value.filter((parameter) => parameter.id !== item.id)
                  )
                }
              >
                <Trash2 />
              </Button>
            </div>
            <Field>
              <Input
                aria-label={tr('parameterDescription')}
                placeholder={tr('parameterDescription')}
                value={item.description}
                maxLength={500}
                onChange={(event) =>
                  update(item.id, { description: event.target.value })
                }
              />
            </Field>
            <Field orientation='horizontal'>
              <Checkbox
                id={`required-${item.id}`}
                checked={item.required}
                onCheckedChange={(checked) =>
                  update(item.id, { required: checked === true })
                }
              />
              <FieldLabel htmlFor={`required-${item.id}`}>
                {tr('required')}
              </FieldLabel>
            </Field>
          </FieldGroup>
        ))}
      </FieldGroup>
    </FieldSet>
  )
}
