import { render, screen, waitFor } from '@testing-library/react'
import { useSSE } from './useSSE'
import { vi } from 'vitest'

function Probe({ reconcile }: { reconcile: (threadId: string) => Promise<void> }) {
  const status = useSSE('thread-1', 'secret-token', reconcile)
  return <span>{status}</span>
}

test('uses a bearer header rather than a URL token and aborts on disconnect', async () => {
  let signal: AbortSignal | undefined
  const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    signal = init?.signal ?? undefined
    expect(String(url)).not.toContain('secret-token')
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer secret-token')
    return { ok: true, body: new ReadableStream() } as Response
  })
  vi.stubGlobal('fetch', fetchMock)
  const reconcile = vi.fn(async () => undefined)
  const view = render(<Probe reconcile={reconcile} />)
  expect(await screen.findByText('connected')).toBeInTheDocument()
  expect(reconcile).toHaveBeenCalledWith('thread-1')
  view.unmount()
  await waitFor(() => expect(signal?.aborted).toBe(true))
  vi.unstubAllGlobals()
})
