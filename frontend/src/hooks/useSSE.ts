import { useEffect, useState } from 'react'
import { eventStreamUrl } from '../api/client'

export function useSSE(
  threadId: string | null,
  token: string | null,
  reconcile?: (threadId: string) => Promise<void>,
) {
  const [status, setStatus] = useState<'idle' | 'connecting' | 'connected' | 'closed'>('idle')
  useEffect(() => {
    if (!threadId || !token) { setStatus('idle'); return }
    const controller = new AbortController()
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null
    const seenTerminalEvents = new Set<string>()
    setStatus('connecting')
    void (async () => {
      while (!controller.signal.aborted) {
        try {
          const response = await fetch(eventStreamUrl(threadId), {
            headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
          })
          if (!response.ok || !response.body) throw new Error('SSE unavailable')
          setStatus('connected')
          await reconcile?.(threadId)
          reader = response.body.getReader()
          const decoder = new TextDecoder()
          let buffered = ''
          while (!controller.signal.aborted) {
            const { done, value } = await reader.read()
            if (done) break
            buffered += decoder.decode(value, { stream: true })
            const frames = buffered.split('\n\n')
            buffered = frames.pop() ?? ''
            for (const frame of frames) {
              const type = frame.split('\n').find((line) => line.startsWith('event: '))?.slice(7)
              if (type && ['workflow_updated', 'message.completed', 'message.failed', 'message.escalated'].includes(type)) {
                if (type.startsWith('message.')) {
                  const dataLine = frame.split('\n').find((line) => line.startsWith('data: '))
                  if (dataLine) {
                    try {
                      const event = JSON.parse(dataLine.slice(6)) as { event_id?: string; run_id?: string }
                      const key = `${event.run_id ?? 'unknown'}:${event.event_id ?? 'unknown'}`
                      if (seenTerminalEvents.has(key)) continue
                      seenTerminalEvents.add(key)
                      if (seenTerminalEvents.size > 256) {
                        const oldest = seenTerminalEvents.values().next().value as string | undefined
                        if (oldest) seenTerminalEvents.delete(oldest)
                      }
                    } catch {
                      // Malformed notifications never replace HTTP state reconciliation.
                    }
                  }
                }
                await reconcile?.(threadId)
              }
            }
          }
        } catch {
          if (controller.signal.aborted) break
          setStatus('closed')
          await reconcile?.(threadId).catch(() => undefined)
        }
        if (!controller.signal.aborted) {
          setStatus('connecting')
          await new Promise<void>((resolve) => {
            const timer = setTimeout(resolve, 1000)
            controller.signal.addEventListener('abort', () => { clearTimeout(timer); resolve() }, { once: true })
          })
        }
      }
    })()
    return () => {
      controller.abort()
      void reader?.cancel().catch(() => undefined)
      setStatus('closed')
    }
  }, [reconcile, threadId, token])
  return status
}
