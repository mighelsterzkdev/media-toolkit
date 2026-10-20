import { useState } from 'react'
import { LoaderCircle, Settings2 } from 'lucide-react'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from './ui/dialog'
import type { DesktopState, Settings } from '../types'

interface Props {
  state: DesktopState; disabled: boolean; busy: boolean;
  onSave(value: Settings): Promise<DesktopState | null>;
  onClearResume(): Promise<DesktopState | null>;
}

export function SettingsDialog({ state, disabled, busy, onSave, onClearResume }: Props) {
  const [open, setOpen] = useState(false)
  const [height, setHeight] = useState('')
  const [size, setSize] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  function changeOpen(value: boolean) {
    if (saving) return
    if (value) {
      setHeight(state.settings.max_height?.toString() ?? '')
      setSize(state.settings.max_size == null ? '' : (state.settings.max_size / 1048576).toString())
      setError(null)
    }
    setOpen(value)
  }
  async function save(event: React.FormEvent) {
    event.preventDefault()
    const maxHeight = height.trim() ? Number(height) : null
    const maxSizeMiB = size.trim() ? Number(size) : null
    if (maxHeight != null && (!Number.isSafeInteger(maxHeight) || maxHeight < 1)) { setError('Maximum video height must be a positive whole number.'); return }
    if (maxSizeMiB != null && (!Number.isFinite(maxSizeMiB) || maxSizeMiB <= 0 || !Number.isSafeInteger(Math.round(maxSizeMiB * 1048576)))) { setError('Maximum file size must be a positive number.'); return }
    setSaving(true)
    const result = await onSave({ output: state.settings.output, max_height: maxHeight, max_size: maxSizeMiB == null ? null : Math.round(maxSizeMiB * 1048576) })
    setSaving(false)
    if (result) setOpen(false)
    else setError('Settings could not be saved. Please check your values and try again.')
  }
  return <Dialog open={open} onOpenChange={changeOpen}>
    <DialogTrigger asChild><Button variant="ghost" size="icon" aria-label="Settings" title="Settings" disabled={disabled}><Settings2 className="size-[18px]" /></Button></DialogTrigger>
    <DialogContent className="settings-dialog" onEscapeKeyDown={event => { if (saving) event.preventDefault() }} onPointerDownOutside={event => { if (saving) event.preventDefault() }}>
      <DialogHeader><DialogTitle>Settings</DialogTitle><DialogDescription>Your limits are remembered on this computer.</DialogDescription></DialogHeader>
      <form onSubmit={save} className="space-y-4">
        <div className="settings-limits">
          <div className="space-y-2"><Label htmlFor="settings-height">Max video height</Label><div className="unit-input"><Input id="settings-height" type="number" min="1" step="1" placeholder="Unlimited" value={height} onChange={event => setHeight(event.target.value)} disabled={saving} /><span>px</span></div></div>
          <div className="space-y-2"><Label htmlFor="settings-size">Max file size</Label><div className="unit-input"><Input id="settings-size" type="number" min="0.000001" step="any" placeholder="Unlimited" value={size} onChange={event => setSize(event.target.value)} disabled={saving} /><span>MiB</span></div></div>
        </div>
        <p className="field-help">Leave limits empty for the best available quality. Height applies to videos; file size applies to all downloads.</p>
        {state.resume_count > 0 ? <div className="resume-settings"><div><p>Unfinished downloads</p><span>{state.resume_count} saved {state.resume_count === 1 ? 'download' : 'downloads'}. Active downloads are kept.</span></div><Button type="button" size="sm" variant="outline" disabled={saving || busy} onClick={() => void onClearResume()}>Clear partials</Button></div> : null}
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
        <DialogFooter><Button type="button" variant="outline" onClick={() => changeOpen(false)} disabled={saving}>Cancel</Button><Button type="submit" disabled={saving || busy}>{saving ? <LoaderCircle className="animate-spin" /> : null}Save settings</Button></DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}
