import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: query.includes('prefers-reduced-motion'),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

Object.defineProperty(window, 'scrollTo', { writable: true, value: vi.fn() })

Object.defineProperty(URL, 'createObjectURL', {
  configurable: true,
  writable: true,
  value: vi.fn(() => 'blob:document-preview'),
})

Object.defineProperty(URL, 'revokeObjectURL', {
  configurable: true,
  writable: true,
  value: vi.fn(),
})
