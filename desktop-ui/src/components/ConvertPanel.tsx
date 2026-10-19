import { useState } from 'react'
import { ArrowRightLeft, CircleAlert, FileImage, LoaderCircle, Plus, X } from 'lucide-react'
import { Button } from './ui/button'
import { Label } from './ui/label'
import { RadioGroup, RadioGroupItem } from './ui/radio-group'
import { SaveDestination } from './SaveDestination'
import { bytes } from '../format'
import type { Conversion, ImageFormat } from '../types'

interface Props {
  conversion: Conversion; output: string; disabled: boolean;
  onAdd(): void; onRemove(id: string): void; onClear(): void; onBrowse(): void;
  onConvert(ids: string[], target: ImageFormat, background: '#FFFFFF' | '#000000'): Promise<unknown>
}

const formats: ImageFormat[] = ['png', 'jpg', 'webp']
const formatNames: Record<ImageFormat, string> = { png: 'PNG', jpg: 'JPG', webp: 'WebP' }

export function ConvertPanel({ conversion, output, disabled, onAdd, onRemove, onClear, onBrowse, onConvert }: Props) {
  // New valid files start selected; user exclusions survive polling and tab changes.
  const [excluded, setExcluded] = useState<Set<string>>(() => new Set())
  const [preferredTarget, setPreferredTarget] = useState<ImageFormat>('jpg')
  const [background, setBackground] = useState<'#FFFFFF' | '#000000'>('#FFFFFF')
  const [submitting, setSubmitting] = useState(false)
  const checking = conversion.status === 'checking'
  const busy = disabled || checking || submitting
  const validFiles = conversion.files.filter(file => !file.error && file.targets.length > 0)
  const selected = validFiles.filter(file => !excluded.has(file.id))
  const targets = formats.filter(target => selected.length > 0 && selected.every(file => file.targets.includes(target)))
  const target = targets.includes(preferredTarget) ? preferredTarget : targets[0]
  const flattensAlpha = target === 'jpg' && selected.some(file => file.transparent)

  function selectFile(id: string, checked: boolean) {
    setExcluded(previous => { const next = new Set(previous); if (checked) next.delete(id); else next.add(id); return next })
  }
  function toggleAll() {
    setExcluded(previous => {
      const next = new Set(previous)
      for (const file of validFiles) { if (selected.length === validFiles.length) next.add(file.id); else next.delete(file.id) }
      return next
    })
  }
  async function convert() {
    if (busy || !target || selected.length === 0) return
    setSubmitting(true)
    try { await onConvert(selected.map(file => file.id), target, background) }
    finally { setSubmitting(false) }
  }

  return <section className="convert-card" aria-labelledby="convert-heading">
    <div className="section-heading new-download-heading">
      <h2 id="convert-heading">Convert images</h2>
      <Button variant="outline" onClick={onAdd} disabled={busy}><Plus className="size-4" />Add files</Button>
    </div>
    {conversion.files.length === 0 ? <div className="conversion-empty">
      <FileImage className="size-6" aria-hidden="true" />
      <div><p>Choose images from your computer</p><span>PNG, JPG and WebP. Original files are kept.</span></div>
    </div> : <>
      <div className="conversion-selection">
        <span>{selected.length} of {validFiles.length} valid {validFiles.length === 1 ? 'image' : 'images'} selected</span>
        <div><Button variant="ghost" size="sm" onClick={toggleAll} disabled={busy || validFiles.length === 0}>{selected.length === validFiles.length ? 'Deselect all' : 'Select all'}</Button><Button variant="ghost" size="sm" onClick={onClear} disabled={busy}>Clear files</Button></div>
      </div>
      <ul className="conversion-files" aria-label="Selected image files">
        {conversion.files.map(file => {
          const valid = !file.error && file.targets.length > 0
          return <li key={file.id} className={`conversion-file ${file.error ? 'conversion-file-invalid' : ''}`}>
            <input type="checkbox" className="file-checkbox" aria-label={`Select ${file.name}`} checked={valid && !excluded.has(file.id)} disabled={busy || !valid} onChange={event => selectFile(file.id, event.target.checked)} />
            <FileImage className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <div className="conversion-file-copy"><p title={file.name}>{file.name}</p>
              {file.error ? <span className="inline-error">{file.error}</span> : <span>{file.format?.toUpperCase()}{file.width && file.height ? ` · ${file.width} × ${file.height}` : ''}{file.size != null ? ` · ${bytes(file.size)}` : ''}{file.transparent ? ' · Transparent' : ''}</span>}
            </div>
            <Button variant="ghost" size="icon" aria-label={`Remove ${file.name}`} title="Remove from selection" disabled={busy} onClick={() => onRemove(file.id)}><X className="size-4" /></Button>
          </li>
        })}
      </ul>
    </>}
    {checking ? <div className="checking-state" role="status"><LoaderCircle className="size-4 animate-spin" />Checking image files…</div> : null}
    {conversion.error ? <div className="inspection-error" role="alert"><CircleAlert className="size-4 shrink-0" /><span>{conversion.error}</span></div> : null}
    {selected.length > 0 && !checking ? targets.length > 0 ? <div className="conversion-options">
      <Label>Convert to</Label>
      <RadioGroup value={target} onValueChange={value => setPreferredTarget(value as ImageFormat)} aria-label="Conversion format" orientation="horizontal" className="kind-options flex flex-wrap gap-2">
        {targets.map(format => <Label key={format} htmlFor={`convert-format-${format}`} className={`kind-option ${format === target ? 'selected' : ''}`}><RadioGroupItem id={`convert-format-${format}`} value={format} className="sr-only" disabled={busy} />{formatNames[format]}</Label>)}
      </RadioGroup>
      {flattensAlpha ? <div className="alpha-background"><Label>JPG background</Label>
        <RadioGroup value={background} onValueChange={value => setBackground(value as '#FFFFFF' | '#000000')} aria-label="JPG background" orientation="horizontal" className="flex flex-wrap gap-6">
          <Label htmlFor="background-white" className="background-choice"><RadioGroupItem id="background-white" value="#FFFFFF" disabled={busy} />White</Label>
          <Label htmlFor="background-black" className="background-choice"><RadioGroupItem id="background-black" value="#000000" disabled={busy} />Black</Label>
        </RadioGroup><p>JPG cannot keep transparency. Transparent areas will use this background.</p>
      </div> : null}
    </div> : <p className="url-hint">These images have no shared output format. Select fewer images to see compatible formats.</p> : null}
    <div className="download-footer">
      <SaveDestination output={output} disabled={disabled} onBrowse={onBrowse} />
      <Button className="download-button" onClick={() => void convert()} disabled={busy || !target || selected.length === 0}>{submitting ? <LoaderCircle className="animate-spin" /> : <ArrowRightLeft />}Convert</Button>
    </div>
  </section>
}
