import { useState } from 'react'
import { ListPlus, LoaderCircle } from 'lucide-react'
import { Button } from './ui/button'
import { Textarea } from './ui/textarea'
import { Label } from './ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from './ui/dialog'
import type { DesktopState } from '../types'
import { t } from '../i18n'

interface Props { disabled: boolean; busy: boolean; onEnqueue(text: string): Promise<DesktopState | null> }
export function BatchDialog({ disabled, busy, onEnqueue }: Props) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!text.trim()) return
    setSubmitting(true)
    setError(null)
    const result = await onEnqueue(text)
    setSubmitting(false)
    if (result) { setText(''); setOpen(false) }
    else setError(t('These links could not be queued. Check the URLs and try again.'))
  }
  return <Dialog open={open} onOpenChange={value => { if (!submitting) { setOpen(value); setError(null) } }}>
    <DialogTrigger asChild><Button variant="ghost" size="sm" disabled={disabled}><ListPlus />{t('Add multiple links')}</Button></DialogTrigger>
    <DialogContent onEscapeKeyDown={event => { if (submitting) event.preventDefault() }} onPointerDownOutside={event => { if (submitting) event.preventDefault() }}>
      <DialogHeader><DialogTitle>{t('Add multiple links')}</DialogTitle><DialogDescription>{t('Each link is checked, then queued in its original format. Duplicate links are skipped.')}</DialogDescription></DialogHeader>
      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-2"><Label htmlFor="batch-links">{t('Links')}</Label><Textarea id="batch-links" value={text} onChange={event => setText(event.target.value)} placeholder={'https://…\nhttps://…'} rows={7} maxLength={1048576} disabled={submitting} spellCheck={false} autoComplete="off" /></div>
        <p className="field-help">{t('One URL per line. For pages with several media items, use Check to choose an item individually.')}</p>
        {error ? <p role="alert" className="inline-error">{error}</p> : null}
        <DialogFooter><Button variant="outline" type="button" disabled={submitting} onClick={() => setOpen(false)}>{t('Cancel')}</Button><Button disabled={submitting || busy || !text.trim()} type="submit">{submitting ? <LoaderCircle className="animate-spin" /> : null}{t('Download originals')}</Button></DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}
