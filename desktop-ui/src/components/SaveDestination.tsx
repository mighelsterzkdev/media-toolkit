import { ChevronRight, Folder } from 'lucide-react'
import { Button } from './ui/button'

interface Props { output: string; disabled: boolean; onBrowse(): void }

export function SaveDestination({ output, disabled, onBrowse }: Props) {
  return <div className="folder-destination">
    <Folder className="size-4 shrink-0" aria-hidden="true" />
    <div><span>Save to</span><p title={output}>{output || 'Your output folder'}</p></div>
    <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={onBrowse}>Browse<ChevronRight className="size-3.5" /></Button>
  </div>
}
