import { useCallback, useEffect, useRef, useState } from 'react'
import { initialState, type DesktopApi, type DesktopState } from './types'

type Method = keyof DesktopApi

export function useDesktop() {
  const [state, setState] = useState<DesktopState>(initialState)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<Method | null>(null)
  const alive = useRef(false)
  const occupied = useRef(false)
  const operationQueue = useRef(Promise.resolve())

  useEffect(() => {
    alive.current = true
    let timer: ReturnType<typeof setTimeout>
    let stopped = false
    let failures = 0
    let polling = false
    const poll = async () => {
      if (stopped || polling) return
      polling = true
      const api = window.pywebview?.api
      if (api && !occupied.current) {
        occupied.current = true
        try {
          const result = await api.get_state()
          if (!stopped) {
            if (result.ok) {
              setState(result.state)
              setConnected(true)
              failures = 0
            } else {
              setError(result.error)
              setConnected(false)
              failures += 1
            }
          }
        } catch {
          if (!stopped) {
            setConnected(false)
            failures += 1
          }
        } finally { occupied.current = false }
      }
      polling = false
      if (!stopped) {
        clearTimeout(timer)
        timer = setTimeout(poll, Math.min(400 * 2 ** failures, 4000))
      }
    }
    const ready = () => { if (!polling) { clearTimeout(timer); void poll() } }
    window.addEventListener('pywebviewready', ready)
    void poll()
    return () => {
      stopped = true
      alive.current = false
      clearTimeout(timer)
      window.removeEventListener('pywebviewready', ready)
    }
  }, [])

  const invoke = useCallback(<K extends Method>(method: K, ...args: Parameters<DesktopApi[K]>): Promise<DesktopState | null> => {
    const execute = async () => {
      if (!alive.current || !window.pywebview?.api) return null
      // Mutations run in order; snapshots never race a newer action.
      while (occupied.current && alive.current) await new Promise(resolve => setTimeout(resolve, 20))
      if (!alive.current) return null
      occupied.current = true
      setPending(method)
      setError(null)
      try {
        const apiMethod = window.pywebview.api[method] as (...params: Parameters<DesktopApi[K]>) => ReturnType<DesktopApi[K]>
        const result = await apiMethod(...args)
        if (!alive.current) return null
        if (result.ok) { setState(result.state); setConnected(true); return result.state }
        setError(result.error)
      } catch {
        if (alive.current) setError('The desktop connection was interrupted. Please try again.')
      } finally {
        occupied.current = false
        if (alive.current) setPending(null)
      }
      return null
    }
    const operation = operationQueue.current.then(execute, execute)
    operationQueue.current = operation.then(() => undefined)
    return operation
  }, [])

  return { state, connected, error, pending, invoke, dismissError: () => setError(null) }
}
