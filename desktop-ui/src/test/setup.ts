import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => { cleanup(); delete window.pywebview })
Object.defineProperty(window, 'ResizeObserver', { value: class { observe() {} unobserve() {} disconnect() {} }, writable: true })
HTMLElement.prototype.scrollIntoView = () => {}
HTMLElement.prototype.hasPointerCapture = () => false
HTMLElement.prototype.setPointerCapture = () => {}
HTMLElement.prototype.releasePointerCapture = () => {}
