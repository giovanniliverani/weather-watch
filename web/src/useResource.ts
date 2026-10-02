// Load one API resource per key, cancel the previous request when the key changes, and remember
// answers in a per-cache Map so a revisit (re-selecting a pin, reopening a tab) does not refetch.
import { useEffect, useState } from 'react'

export type Resource<T> =
  | { state: 'idle' }
  | { state: 'loading'; previous?: T }
  | { state: 'ready'; data: T }
  | { state: 'error'; error: Error }

interface Tagged<T> {
  key: string
  value: Resource<T>
}

export function useResource<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  cache?: Map<string, T>,
): Resource<T> {
  const [result, setResult] = useState<Tagged<T> | null>(null)

  useEffect(() => {
    if (key === null || cache?.has(key)) return
    const controller = new AbortController()
    load(controller.signal).then(
      (data) => {
        cache?.set(key, data)
        setResult({ key, value: { state: 'ready', data } })
      },
      (error: unknown) => {
        if (controller.signal.aborted) return
        setResult({ key, value: { state: 'error', error: error instanceof Error ? error : new Error(String(error)) } })
      },
    )
    return () => controller.abort()
    // `load` is recreated each render; the key alone decides when to fetch.
  }, [key, cache])

  if (key === null) return { state: 'idle' }
  const cached = cache?.get(key)
  if (cached !== undefined) return { state: 'ready', data: cached }
  if (result?.key === key) return result.value
  // While a new key loads, hand back the last answer so the screen does not blank between filters.
  return { state: 'loading', previous: result?.value.state === 'ready' ? result.value.data : undefined }
}
