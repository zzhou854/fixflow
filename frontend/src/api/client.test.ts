import { api, shanghaiReferenceTime } from './client'
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

test('sends an Asia/Shanghai reference time with the matching UTC offset', async () => {
  expect(shanghaiReferenceTime(new Date('2026-07-27T04:05:06.000Z')))
    .toBe('2026-07-27T12:05:06.000+08:00')

  const fetchMock = vi.fn<
    (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
  >()
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) } as Response)
  vi.stubGlobal('fetch', fetchMock)
  await api.createThread('token', 'property', '厨房漏水', 'time-key')
  const [, init] = fetchMock.mock.calls[0]!
  const payload = JSON.parse(String(init?.body)) as { reference_time: string }
  expect(payload.reference_time).toMatch(/\+08:00$/)
  vi.unstubAllGlobals()
})
