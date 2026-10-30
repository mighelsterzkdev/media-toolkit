import * as RadioGroupPrimitive from '@radix-ui/react-radio-group'

export interface FormatChoice { id: string; label: string; detail?: string }
export interface FormatGroup { label?: string; choices: FormatChoice[] }
interface Props {
  id: string; label: string; groups: FormatGroup[]; value: string | undefined; disabled?: boolean; describedBy?: string;
  onChange(value: string): void
}

// Every output is visible at once as a chip; arrow keys move across all groups.
export function FormatPicker({ id, label, groups, value, disabled, describedBy, onChange }: Props) {
  return <div className="format-field">
    <span id={`${id}-label`} className="format-field-label">{label}</span>
    <RadioGroupPrimitive.Root id={id} value={value ?? ''} onValueChange={onChange} disabled={disabled} aria-labelledby={`${id}-label`}
      aria-describedby={describedBy} className="format-groups" loop>
      {groups.map((group, index) => <div key={group.label ?? index} className="format-group" role={group.label ? 'group' : undefined} aria-label={group.label}>
        {group.label ? <span className={groups.length > 1 ? 'format-group-label' : 'sr-only'}>{group.label}</span> : null}
        <div className="format-chips">{group.choices.map(choice => <RadioGroupPrimitive.Item key={choice.id} value={choice.id} className="format-chip">
          <span>{choice.label}</span>{choice.detail ? <small>{choice.detail}</small> : null}
        </RadioGroupPrimitive.Item>)}</div>
      </div>)}
    </RadioGroupPrimitive.Root>
  </div>
}
