import { computed, getCurrentInstance, onUnmounted, ref, type ComputedRef, type Ref } from 'vue'

import {
  AgentApiError,
  AgentHttpClient,
  AgentNetworkError,
  AgentRequestAbortedError,
} from '../api/client'
import type { SessionHistoryItem } from '../domain/history'

const SAFE_FALLBACK_MESSAGE = 'Agent 服务请求失败'

export interface SessionHistoryError {
  readonly code: string
  readonly message: string
}

export interface UseSessionHistoryOptions {
  readonly client?: AgentHttpClient
  readonly autoLoad?: boolean
}

export interface SessionHistoryController {
  readonly items: Ref<readonly SessionHistoryItem[]>
  readonly loading: Ref<boolean>
  readonly error: Ref<SessionHistoryError | null>
  readonly hasMore: ComputedRef<boolean>
  readonly refresh: () => Promise<void>
  readonly loadMore: () => Promise<void>
}

function isIgnorableAbort(error: unknown): boolean {
  return (
    error instanceof AgentRequestAbortedError ||
    (error instanceof DOMException && error.name === 'AbortError')
  )
}

function toSafeHistoryError(error: unknown): SessionHistoryError {
  if (error instanceof AgentApiError || error instanceof AgentNetworkError) {
    return { code: error.code, message: error.message }
  }
  if (error instanceof AgentRequestAbortedError) {
    return { code: error.code, message: error.message }
  }
  return { code: 'internal_error', message: SAFE_FALLBACK_MESSAGE }
}

function appendUnique(
  current: readonly SessionHistoryItem[],
  incoming: readonly SessionHistoryItem[],
): SessionHistoryItem[] {
  const seen = new Set(current.map((item) => item.session_id))
  const merged = [...current]
  for (const item of incoming) {
    if (seen.has(item.session_id)) {
      continue
    }
    seen.add(item.session_id)
    merged.push(item)
  }
  return merged
}

export function useSessionHistory(
  options: UseSessionHistoryOptions = {},
): SessionHistoryController {
  const client = options.client ?? new AgentHttpClient()
  const items = ref<readonly SessionHistoryItem[]>([])
  const loading = ref(false)
  const error = ref<SessionHistoryError | null>(null)
  const nextCursor = ref<string | null>(null)
  const hasMore = computed(() => nextCursor.value !== null)

  let requestGeneration = 0
  let inFlight: 'refresh' | 'more' | null = null
  let abortController: AbortController | null = null

  function abortInFlight(): void {
    abortController?.abort()
    abortController = null
  }

  async function refresh(): Promise<void> {
    abortInFlight()
    const generation = requestGeneration + 1
    requestGeneration = generation
    const controller = new AbortController()
    abortController = controller
    inFlight = 'refresh'
    error.value = null
    loading.value = true
    try {
      const page = await client.listSessionHistory({ signal: controller.signal })
      if (generation !== requestGeneration) {
        return
      }
      items.value = [...page.sessions]
      nextCursor.value = page.next_cursor
      error.value = null
    } catch (caught) {
      if (generation !== requestGeneration || isIgnorableAbort(caught)) {
        return
      }
      error.value = toSafeHistoryError(caught)
    } finally {
      if (generation === requestGeneration) {
        loading.value = false
        inFlight = null
        if (abortController === controller) {
          abortController = null
        }
      }
    }
  }

  async function loadMore(): Promise<void> {
    const cursor = nextCursor.value
    if (cursor === null || inFlight !== null || loading.value) {
      return
    }
    const generation = requestGeneration
    const controller = new AbortController()
    abortController = controller
    inFlight = 'more'
    try {
      const page = await client.listSessionHistory({
        cursor,
        signal: controller.signal,
      })
      if (generation !== requestGeneration) {
        return
      }
      items.value = appendUnique(items.value, page.sessions)
      nextCursor.value = page.next_cursor
      error.value = null
    } catch (caught) {
      if (generation !== requestGeneration || isIgnorableAbort(caught)) {
        return
      }
      error.value = toSafeHistoryError(caught)
    } finally {
      if (generation === requestGeneration) {
        inFlight = null
        if (abortController === controller) {
          abortController = null
        }
      }
    }
  }

  if (getCurrentInstance() !== null) {
    onUnmounted(() => {
      requestGeneration += 1
      abortInFlight()
    })
  }

  if (options.autoLoad !== false) {
    void refresh()
  }

  return {
    items,
    loading,
    error,
    hasMore,
    refresh,
    loadMore,
  }
}
