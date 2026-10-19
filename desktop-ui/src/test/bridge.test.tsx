import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useDesktop } from '../use-desktop'
import { initialState, type ApiResult, type DesktopApi } from '../types'

afterEach(() => vi.useRealTimers())

function stateApi(getState: () => Promise<ApiResult>) {
  window.pywebview = { api: { get_state: getState } as DesktopApi }
}

describe('desktop bridge polling', () => {
  it('does not overlap polling or create duplicate loops from ready events', async () => {
    vi.useFakeTimers()
    let resolve!: (value: ApiResult) => void
    const first = new Promise<ApiResult>(done => { resolve = done })
    const get = vi.fn().mockReturnValueOnce(first).mockResolvedValue({ ok: true, state: structuredClone(initialState) })
    stateApi(get)
    const { unmount } = renderHook(useDesktop)
    act(() => {
      window.dispatchEvent(new Event('pywebviewready'))
      window.dispatchEvent(new Event('pywebviewready'))
    })
    await act(() => vi.advanceTimersByTimeAsync(1200))
    expect(get).toHaveBeenCalledTimes(1)
    await act(async () => resolve({ ok: true, state: structuredClone(initialState) }))
    await act(() => vi.advanceTimersByTimeAsync(400))
    expect(get).toHaveBeenCalledTimes(2)
    await act(() => vi.advanceTimersByTimeAsync(400))
    expect(get).toHaveBeenCalledTimes(3)
    unmount()
    await act(() => vi.advanceTimersByTimeAsync(2000))
    expect(get).toHaveBeenCalledTimes(3)
  })

  it('backs off failed reads and reconnects with the latest snapshot', async () => {
    vi.useFakeTimers()
    const get = vi.fn().mockRejectedValueOnce(new Error('Host busy')).mockResolvedValue({ ok: true, state: { ...structuredClone(initialState), version: '0.7.0' } })
    stateApi(get)
    const { result } = renderHook(useDesktop)
    await act(() => vi.advanceTimersByTimeAsync(0))
    expect(result.current.connected).toBe(false)
    await act(() => vi.advanceTimersByTimeAsync(799))
    expect(get).toHaveBeenCalledTimes(1)
    await act(() => vi.advanceTimersByTimeAsync(1))
    expect(get).toHaveBeenCalledTimes(2)
    expect(result.current.connected).toBe(true)
    expect(result.current.state.version).toBe('0.7.0')
  })
})
