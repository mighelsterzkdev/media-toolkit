import { useState } from 'react'
import { ArrowDownToLine, ArrowRight, ArrowRightLeft, CircleAlert, FileImage, FileVideo, Link2, LoaderCircle, Music2, X } from 'lucide-react'
import { Button } from './components/ui/button'
import { Input } from './components/ui/input'
import { Label } from './components/ui/label'
import { RadioGroup, RadioGroupItem } from './components/ui/radio-group'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './components/ui/select'
import { BatchDialog } from './components/BatchDialog'
import { SettingsDialog } from './components/SettingsDialog'
import { TaskList } from './components/TaskList'
import { ConvertPanel } from './components/ConvertPanel'
import { SaveDestination } from './components/SaveDestination'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './components/ui/tabs'
import { useDesktop } from './use-desktop'
import { normalizeUrlInput, sourceHost } from './format'
import type { MediaAsset } from './types'

interface Selection { inspection: string | null; asset: string; kind: string; option: string }
const thumbnailPattern = /^data:image\/(?:png|jpeg|webp);base64,[a-z\d+/=\s]+$/i
function mediaIcon(kind: string) { return kind === 'audio' ? Music2 : kind === 'image' ? FileImage : FileVideo }
function defaultSelection(inspection: string | null, asset: MediaAsset): Selection {
  const kind = asset.kinds.find(item => item.id === asset.default_kind) ?? asset.kinds[0]
  return { inspection, asset: asset.id, kind: kind?.id ?? '', option: kind?.options.find(option => option.recommended)?.id ?? kind?.options[0]?.id ?? '' }
}

export default function App() {
  const { state, connected, error, pending, invoke, dismissError } = useDesktop()
  const [tab, setTab] = useState('download')
  const [url, setUrl] = useState('')
  const [checkedDraft, setCheckedDraft] = useState<string | null>(null)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [downloadBusy, setDownloadBusy] = useState(false)
  const normalizedUrl = normalizeUrlInput(url)
  const currentInspection = checkedDraft === normalizedUrl && state.inspection.url === normalizedUrl
  const checking = currentInspection && state.inspection.status === 'checking'
  const ready = currentInspection && state.inspection.status === 'ready'
  const inspectionError = currentInspection && state.inspection.status === 'failed' ? state.inspection.error : null
  const assets = ready ? state.inspection.assets : []
  const selectedAsset = selection?.inspection === state.inspection.id ? assets.find(asset => asset.id === selection.asset) : null
  // A page with alternatives requires an explicit asset choice; a single item needs no extra step.
  const asset = selectedAsset ?? (assets.length === 1 ? assets[0] : null)
  const chosen = asset ? selection?.inspection === state.inspection.id && selection.asset === asset.id ? selection : defaultSelection(state.inspection.id, asset) : null
  const kind = asset?.kinds.find(item => item.id === chosen?.kind) ?? asset?.kinds[0]
  const option = kind?.options.find(item => item.id === chosen?.option) ?? kind?.options.find(item => item.recommended) ?? kind?.options[0]
  const disconnected = !connected
  const busy = pending != null
  const installing = state.tools.status === 'installing'
  const toolsReady = state.tools.status === 'ready'
  const showToolsNotice = tab === 'download' ? !toolsReady : state.tools.ffmpeg === false
  const toolsMessage = tab === 'convert' && !installing && state.tools.status !== 'failed'
    ? 'Install FFmpeg for audio and video conversion. Images can still be converted offline.'
    : state.tools.message || state.tools.error || (installing ? 'Installing media tools…' : 'Install media tools to enable downloads. Local image conversion is ready to use.')
  const canDownload = connected && toolsReady && ready && asset && kind && option && !downloadBusy && !busy
  const Icon = mediaIcon(kind?.id ?? '')

  async function check(event: React.FormEvent) {
    event.preventDefault()
    if (!normalizedUrl || !connected || busy || !toolsReady) return
    setCheckedDraft(normalizedUrl)
    setSelection(null)
    await invoke('check_url', normalizedUrl)
  }
  function editUrl(value: string) {
    setUrl(value)
    setCheckedDraft(null)
    setSelection(null)
    dismissError()
  }
  async function download() {
    if (!canDownload || !asset || !kind || !option || !state.inspection.id) return
    setDownloadBusy(true)
    await invoke('enqueue', state.inspection.id, asset.id, kind.id, option.id)
    setDownloadBusy(false)
  }
  function changeKind(value: string) {
    if (!asset) return
    const nextKind = asset.kinds.find(item => item.id === value)
    if (nextKind) setSelection({ inspection: state.inspection.id, asset: asset.id, kind: value, option: nextKind.options.find(item => item.recommended)?.id ?? nextKind.options[0]?.id ?? '' })
  }
  const activeCount = state.tasks.filter(task => ['queued', 'checking', 'downloading', 'converting', 'finalizing', 'cancelling'].includes(task.status)).length

  async function prepareConversion(id: string) {
    const result = await invoke('prepare_conversion', id)
    if (result) setTab('convert')
  }

  return <Tabs value={tab} onValueChange={setTab} className="app-shell">
    <header className="app-header">
      <div className="app-header-main"><h1>Media Toolkit</h1>
        <TabsList aria-label="Media tools"><TabsTrigger value="download"><ArrowDownToLine className="size-4" />Download</TabsTrigger><TabsTrigger value="convert"><ArrowRightLeft className="size-4" />Convert</TabsTrigger></TabsList>
      </div>
      <SettingsDialog state={state} disabled={disconnected} busy={busy} onSave={value => invoke('save_settings', value)} onClearResume={() => invoke('clear_resume')} />
    </header>

    {!connected || error || state.notice || showToolsNotice ? <div className="app-notices">
    {!connected ? <div className="notice" role="status"><CircleAlert className="size-4 shrink-0" /><span>Waiting for the desktop connection. Open this app with Media Toolkit.</span></div> : null}
    {error ? <div className="notice notice-error" role="alert"><CircleAlert className="size-4 shrink-0" /><span>{error}</span><Button variant="ghost" size="icon" aria-label="Dismiss error" onClick={dismissError}><X /></Button></div> : null}
    {state.notice ? <div className="notice" role="status"><span>{state.notice}</span></div> : null}
    {connected && showToolsNotice ? <div className="notice tools-notice" role="status"><CircleAlert className="size-4 shrink-0" /><span>{toolsMessage}</span><Button variant="outline" size="sm" disabled={installing || busy} onClick={() => void invoke('install_tools')}>{installing ? <LoaderCircle className="animate-spin" /> : null}{state.tools.status === 'failed' ? 'Retry installation' : 'Install tools'}</Button></div> : null}
    </div> : null}

    <main className="app-content">
      <TabsContent value="download" forceMount hidden={tab !== 'download'} className="tool-panel">
      <section className="download-card" aria-labelledby="new-download-heading">
        <div className="section-heading new-download-heading"><h2 id="new-download-heading">New download</h2><BatchDialog disabled={disconnected || !toolsReady} busy={busy || installing} onEnqueue={text => invoke('enqueue_links', text)} /></div>
        <form onSubmit={check} className="url-form">
          <Label htmlFor="media-url" className="sr-only">Media URL</Label>
          <div className="url-input"><Link2 className="size-[18px]" aria-hidden="true" /><Input id="media-url" value={url} onChange={event => editUrl(event.target.value)} placeholder="Paste a video, audio or image link" autoComplete="off" spellCheck={false} disabled={disconnected} maxLength={16384} /></div>
          {checking ? <Button type="button" variant="outline" onClick={() => void invoke('cancel_check')} disabled={busy}><LoaderCircle className="animate-spin" /><span>Cancel</span></Button> : <Button type="submit" variant="secondary" disabled={!normalizedUrl || disconnected || busy || !toolsReady}>Check<ArrowRight /></Button>}
        </form>
        {checking ? <div className="checking-state" role="status"><LoaderCircle className="size-4 animate-spin" />Checking the link and available media…</div> : null}
        {inspectionError ? <div className="inspection-error" role="alert"><CircleAlert className="size-[18px] shrink-0" /><div><p>Could not check this link</p><span>{inspectionError}</span></div></div> : null}
        {ready && assets.length > 1 ? <div className="asset-picker"><Label>Choose an item</Label><RadioGroup value={asset?.id ?? ''} onValueChange={value => { const next = assets.find(item => item.id === value); if (next) setSelection(defaultSelection(state.inspection.id, next)) }} aria-label="Media item" className="asset-options">
          {assets.map((item, index) => <Label key={item.id} htmlFor={`asset-${index}`} className={`asset-option ${asset?.id === item.id ? 'selected' : ''}`}><RadioGroupItem id={`asset-${index}`} value={item.id} /><span><strong>{item.title}</strong>{item.description ? <small>{item.description}</small> : null}</span></Label>)}
        </RadioGroup></div> : null}
        {ready && asset ? <div className="inspected-result">
          <div className="media-summary">
            {asset.thumbnail && thumbnailPattern.test(asset.thumbnail) ? <img className="media-thumbnail" src={asset.thumbnail} alt="" /> : <div className="media-thumbnail media-placeholder"><Icon className="size-7" aria-hidden="true" /></div>}
            <div className="media-description"><span className="source-name">{sourceHost(normalizedUrl)}</span><h3 title={asset.title}>{asset.title}</h3>{asset.description ? <p>{asset.description}</p> : null}</div>
          </div>
          {kind && option ? <div className="download-options">
            <RadioGroup value={kind.id} onValueChange={changeKind} aria-label="Media type" orientation="horizontal" className="kind-options flex flex-wrap gap-2">
              {asset.kinds.map(item => { const KindIcon = mediaIcon(item.id); return <Label key={item.id} className={`kind-option ${kind.id === item.id ? 'selected' : ''}`} htmlFor={`kind-${item.id}`}><RadioGroupItem id={`kind-${item.id}`} value={item.id} className="sr-only" /><KindIcon className="size-4" />{item.label}</Label> })}
            </RadioGroup>
            <div className="download-format">
              <div className="download-target-field"><Label htmlFor="download-format">Save as</Label>
                <Select value={option.id} onValueChange={value => setSelection({ inspection: state.inspection.id, asset: asset.id, kind: kind.id, option: value })} disabled={disconnected || busy || downloadBusy}>
                  <SelectTrigger id="download-format" aria-describedby={option.description ? 'download-format-description' : undefined}><SelectValue /></SelectTrigger>
                  <SelectContent>{kind.options.map(item => <SelectItem key={item.id} value={item.id} textValue={item.label}>{item.label}{item.recommended ? <span className="text-sm text-muted-foreground"> · Recommended</span> : null}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              {option.description ? <p id="download-format-description" className="field-help">{option.description}</p> : null}
            </div>
          </div> : <p className="url-hint px-0">No format fits current limits. Change settings and check again.</p>}
        </div> : null}
        {!checking && !ready && !inspectionError ? <p className="url-hint">Check a link to see the available files and formats.</p> : null}
        {ready && assets.length === 0 ? <p className="url-hint">No downloadable media was found at this link.</p> : null}
        <div className="download-footer">
          <SaveDestination output={state.settings.output} disabled={disconnected || busy} onBrowse={() => void invoke('browse_folder')} />
          <Button className="download-button" onClick={() => void download()} disabled={!canDownload}>{downloadBusy ? <LoaderCircle className="animate-spin" /> : <ArrowDownToLine />}Download</Button>
        </div>
      </section>
      </TabsContent>
      <TabsContent value="convert" forceMount hidden={tab !== 'convert'} className="tool-panel">
        <ConvertPanel conversion={state.conversion} output={state.settings.output} disabled={disconnected || busy} ffmpegReady={state.tools.ffmpeg !== false} onAdd={() => void invoke('select_conversion_files')} onRemove={id => void invoke('remove_conversion_file', id)} onClear={() => void invoke('clear_conversion_files')} onBrowse={() => void invoke('browse_folder')} onConvert={(ids, target, background) => invoke('enqueue_conversions', ids, target, background)} />
      </TabsContent>
      <TaskList tasks={state.tasks} disabled={disconnected || busy} retryDisabled={!toolsReady} onCancel={id => void invoke('cancel_task', id)} onRetry={id => void invoke('retry_task', id)} onOpen={id => void invoke('open_folder', id)} onClear={() => void invoke('remove_finished')} onConvert={id => void prepareConversion(id)} />
    </main>
    <footer className="app-footer"><span>{activeCount > 0 ? `${activeCount} active ${activeCount === 1 ? 'task' : 'tasks'}` : 'Ready when you are'}</span><span>{state.version ? `v${state.version}` : 'Desktop'}</span></footer>
  </Tabs>
}
