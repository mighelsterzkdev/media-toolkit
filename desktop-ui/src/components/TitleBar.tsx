import { useEffect, useState, type MouseEvent } from 'react'
import type { ResizeEdge, WindowAction } from '../types'

const edges: ResizeEdge[] = ['top', 'bottom', 'left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right']

// The header doubles as the window caption; these elements keep their own clicks.
const interactive = 'button, a, input, select, textarea, label, [role="tab"], [role="dialog"], [data-no-drag]'

function send(action: WindowAction) {
  void window.pywebview?.api.window_action?.(action)
}

function startFrom(action: WindowAction) {
  return (event: MouseEvent) => {
    if (event.button !== 0) return
    event.preventDefault()
    send(action)
  }
}

function isMaximized() {
  const { availWidth, availHeight } = window.screen
  return availWidth > 0 && window.outerWidth >= availWidth && window.outerHeight >= availHeight
}

function useMaximized() {
  const [maximized, setMaximized] = useState(isMaximized)
  useEffect(() => {
    const update = () => setMaximized(isMaximized())
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])
  return maximized
}

// Like a native caption, start moving only after the pointer travels a few
// pixels, so single and double clicks never enter Windows' move loop.
export function dragFromHeader(event: MouseEvent) {
  if (event.button !== 0 || (event.target as Element).closest(interactive)) return
  event.preventDefault()
  const { screenX, screenY } = event
  const stop = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', stop)
  }
  const move = (moved: globalThis.MouseEvent) => {
    if (Math.abs(moved.screenX - screenX) < 4 && Math.abs(moved.screenY - screenY) < 4) return
    stop()
    send('drag')
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', stop)
}

export function maximizeFromHeader(event: MouseEvent) {
  if (!(event.target as Element).closest(interactive)) send('maximize')
}

/** Windows 11 caption buttons, drawn with the system Segoe Fluent Icons glyphs. */
export function WindowControls() {
  const maximized = useMaximized()
  return <>
    {maximized ? null : <div className="resize-edges" aria-hidden="true">
      {edges.map(edge => <span key={edge} className={edge} onMouseDown={startFrom(`resize-${edge}`)} />)}
    </div>}
    <div className="window-controls" data-no-drag>
      <button type="button" aria-label="Minimize" title="Minimize" onClick={() => send('minimize')}>{'\uE921'}</button>
      <button type="button" aria-label={maximized ? 'Restore' : 'Maximize'} title={maximized ? 'Restore' : 'Maximize'} onClick={() => send('maximize')}>{maximized ? '\uE923' : '\uE922'}</button>
      <button type="button" className="window-close" aria-label="Close" title="Close" onClick={() => send('close')}>{'\uE8BB'}</button>
    </div>
  </>
}
