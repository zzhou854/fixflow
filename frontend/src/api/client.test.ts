import { api } from './client'
import { vi } from 'vitest'

test('sends the stable API idempotency key in a header rather than the payload', async () => {
  const fetchMock = vi.fn<
    (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
  >()
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) } as Response)
  vi.stubGlobal('fetch', fetchMock)
  await api.createThread('token', 'property', '厨房漏水', 'stable-key')
  const [url, init] = fetchMock.mock.calls[0]!
  expect(String(url)).not.toContain('stable-key')
  expect(new Headers(init?.headers).get('Idempotency-Key')).toBe('stable-key')
  expect(String(init?.body)).not.toContain('stable-key')
  vi.unstubAllGlobals()
})
