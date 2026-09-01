import fixture from '../domain/fixtures/transport-v1.json'
import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import { AgentHttpClient } from '../api/client'
import type { ListSessionHistoryOptions } from '../api/client'
import type { SessionHistoryPage } from '../domain/history'
import { parseSessionHistoryPage } from '../domain/history'
import { useSessionHistory } from './useSessionHistory'

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function later(): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, 0))
}

function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason?: unknown) => void
} {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const PAGE_TWO_SESSION = {
  session_id: 'session_fixture_2',
  first_user_message: {
    text: 'page two',
    truncated: false,
    original_length: 8,
    limit: 4096,
    encoding: 'utf-8',
  },
  created_at: '2026-09-01T08:02:00.000000Z',
  updated_at: '2026-09-01T08:03:00.000000Z',
  last_sequence: 2,
  last_state: 'COMPLETED_TURN',
  resumable: true,
  diagnostics: [],
}

function listCalls(fetchImpl: ReturnType<typeof vi.fn>): string[] {
  return fetchImpl.mock.calls
    .map((call) => String(call[0]))
    .filter((url) => url.includes('/session-history') && !url.includes('/events'))
}

function viewState(history: ReturnType<typeof useSessionHistory>): 'loading' | 'empty' | 'error' | 'ready' {
  if (history.loading.value) {
    return 'loading'
  }
  if (history.error.value !== null) {
    return 'error'
  }
  if (history.items.value.length === 0) {
    return 'empty'
  }
  return 'ready'
}

function stubClient(
  listSessionHistory: (options?: ListSessionHistoryOptions) => Promise<SessionHistoryPage>,
): AgentHttpClient {
  return { listSessionHistory } as unknown as AgentHttpClient
}

describe('useSessionHistory', () => {
  it('loads the first page without a cursor or default limit', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toContain('/session-history')
      expect(init?.method).toBe('GET')
      return jsonResponse(fixture.history.list)
    })
    const history = useSessionHistory({
      client: new AgentHttpClient({ fetchImpl }),
    })

    expect(history.loading.value).toBe(true)
    expect(viewState(history)).toBe('loading')
    await flushPromises()

    expect(listCalls(fetchImpl)).toEqual(['/api/v1/session-history'])
    expect(fetchImpl.mock.calls[0]?.[1]).toMatchObject({ method: 'GET' })
    expect(history.items.value).toHaveLength(1)
    expect(history.items.value[0]?.session_id).toBe('session_fixture_1')
    expect(history.items.value[0]?.first_user_message).toEqual(
      fixture.history.list.sessions[0].first_user_message,
    )
    expect(history.hasMore.value).toBe(true)
    expect(history.loading.value).toBe(false)
    expect(history.error.value).toBeNull()
    expect(viewState(history)).toBe('ready')
  })

  it('echoes next_cursor verbatim on loadMore and appends items', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('cursor=')) {
        return jsonResponse({
          sessions: [PAGE_TWO_SESSION],
          next_cursor: null,
        })
      }
      return jsonResponse(fixture.history.list)
    })
    const history = useSessionHistory({
      client: new AgentHttpClient({ fetchImpl }),
    })
    await flushPromises()

    await history.loadMore()

    expect(listCalls(fetchImpl)).toEqual([
      '/api/v1/session-history',
      '/api/v1/session-history?cursor=opaque_history_cursor_fixture_1',
    ])
    expect(history.items.value.map((item) => item.session_id)).toEqual([
      'session_fixture_1',
      'session_fixture_2',
    ])
    expect(history.hasMore.value).toBe(false)
  })

  it('passes an opaque injected cursor through unchanged and does not decode it', async () => {
    const injected = 'offset=10&path=/secret'
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('cursor=')) {
        return jsonResponse({ sessions: [PAGE_TWO_SESSION], next_cursor: null })
      }
      return jsonResponse({
        sessions: fixture.history.list.sessions,
        next_cursor: injected,
      })
    })
    const history = useSessionHistory({
      client: new AgentHttpClient({ fetchImpl }),
    })
    await flushPromises()
    await history.loadMore()

    expect(listCalls(fetchImpl)[1]).toBe(
      `/api/v1/session-history?cursor=${encodeURIComponent(injected)}`,
    )
    expect(listCalls(fetchImpl)[1]).not.toContain('path=/secret')
  })

  it('sets hasMore to false when next_cursor is null', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ sessions: fixture.history.list.sessions, next_cursor: null }),
    )
    const history = useSessionHistory({
      client: new AgentHttpClient({ fetchImpl }),
    })
    await flushPromises()

    expect(history.hasMore.value).toBe(false)
    const callsBefore = fetchImpl.mock.calls.length
    await history.loadMore()
    expect(fetchImpl.mock.calls).toHaveLength(callsBefore)
  })

  it('refresh clears a previous error and reloads from the first page', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        { error: { code: 'history_unavailable', message: 'private traceback /secret/path.jsonl' } },
        422,
      ),
    )
    const history = useSessionHistory({
      client: new AgentHttpClient({ fetchImpl }),
      autoLoad: false,
    })

    await history.refresh()
    expect(viewState(history)).toBe('error')
    expect(history.error.value?.code).toBe('history_unavailable')

    fetchImpl.mockImplementation(async () => jsonResponse(fixture.history.list))
    const pending = history.refresh()
    expect(history.error.value).toBeNull()
    expect(history.loading.value).toBe(true)
    await pending

    expect(listCalls(fetchImpl).at(-1)).toBe('/api/v1/session-history')
    expect(history.error.value).toBeNull()
    expect(history.items.value).toHaveLength(1)
    expect(history.hasMore.value).toBe(true)
    expect(viewState(history)).toBe('ready')
  })

  it('keeps loading, empty, and error states mutually exclusive', async () => {
    const first = deferred<Response>()
    const fetchImpl = vi.fn(async () => first.promise)
    const history = useSessionHistory({
      client: new AgentHttpClient({ fetchImpl }),
      autoLoad: false,
    })

    expect(viewState(history)).toBe('empty')
    expect(history.loading.value).toBe(false)
    expect(history.items.value).toHaveLength(0)
    expect(history.error.value).toBeNull()

    const loadingRefresh = history.refresh()
    expect(viewState(history)).toBe('loading')
    expect(history.error.value).toBeNull()

    first.resolve(jsonResponse({ sessions: [], next_cursor: null }))
    await loadingRefresh
    expect(viewState(history)).toBe('empty')
    expect(history.loading.value).toBe(false)
    expect(history.items.value).toHaveLength(0)
    expect(history.error.value).toBeNull()

    fetchImpl.mockImplementation(async () =>
      jsonResponse({ error: { code: 'history_not_found', message: 'DROP TABLE sessions' } }, 404),
    )
    await history.refresh()
    expect(viewState(history)).toBe('error')
    expect(history.loading.value).toBe(false)
    expect(history.error.value).not.toBeNull()
  })

  it('maps stable error codes to client-owned prompts and never surfaces backend private text', async () => {
    const privateBody = 'private traceback /secret/path.jsonl DROP TABLE sessions'
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ error: { code: 'invalid_history_cursor', message: privateBody } }, 400),
    )
    const history = useSessionHistory({
      client: new AgentHttpClient({ fetchImpl }),
      autoLoad: false,
    })

    await history.refresh()
    expect(history.error.value).toEqual({
      code: 'invalid_history_cursor',
      message: 'history cursor is invalid',
    })
    expect(JSON.stringify(history.error.value)).not.toContain('private')
    expect(JSON.stringify(history.error.value)).not.toContain('/secret')
    expect(JSON.stringify(history.error.value)).not.toContain('DROP TABLE')

    fetchImpl.mockImplementation(async () =>
      jsonResponse({ error: { code: 'mystery_code', message: privateBody } }, 500),
    )
    await history.refresh()
    expect(history.error.value?.message).toBe('Agent 服务请求失败')
    expect(history.error.value?.message).not.toContain(privateBody)
  })

  it('does not re-enter loadMore or duplicate items when triggered repeatedly', async () => {
    const more = deferred<SessionHistoryPage>()
    const listSessionHistory = vi.fn(async (options?: ListSessionHistoryOptions) => {
      if (options?.cursor !== undefined) {
        return more.promise
      }
      return parseSessionHistoryPage(fixture.history.list)
    })
    const history = useSessionHistory({
      client: stubClient(listSessionHistory),
      autoLoad: false,
    })
    await history.refresh()

    const first = history.loadMore()
    const second = history.loadMore()
    const third = history.loadMore()
    await later()

    expect(listSessionHistory.mock.calls.filter((call) => call[0]?.cursor !== undefined)).toHaveLength(1)
    expect(listSessionHistory.mock.calls[1]?.[0]?.cursor).toBe('opaque_history_cursor_fixture_1')

    more.resolve(
      parseSessionHistoryPage({
        sessions: [fixture.history.list.sessions[0], PAGE_TWO_SESSION],
        next_cursor: null,
      }),
    )
    await Promise.all([first, second, third])

    expect(history.items.value.map((item) => item.session_id)).toEqual([
      'session_fixture_1',
      'session_fixture_2',
    ])
  })

  it('ignores a stale in-flight loadMore when refresh starts', async () => {
    const more = deferred<SessionHistoryPage>()
    const listSessionHistory = vi.fn(async (options?: ListSessionHistoryOptions) => {
      if (options?.cursor !== undefined) {
        return more.promise
      }
      return parseSessionHistoryPage(fixture.history.list)
    })
    const history = useSessionHistory({
      client: stubClient(listSessionHistory),
      autoLoad: false,
    })
    await history.refresh()
    void history.loadMore()
    await history.refresh()

    more.resolve(
      parseSessionHistoryPage({
        sessions: [PAGE_TWO_SESSION],
        next_cursor: null,
      }),
    )
    await flushPromises()

    expect(history.items.value.map((item) => item.session_id)).toEqual(['session_fixture_1'])
    expect(history.hasMore.value).toBe(true)
  })

  it('keeps items that survived the parser and does not fail the list on diagnostics', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({
        sessions: [
          {
            ...fixture.history.list.sessions[0],
            diagnostics: [{ code: 'incomplete_tail', message: 'history has an incomplete final record' }],
          },
          { session_id: '../outside' },
          { first_user_message: { text: 'orphan' } },
          {
            session_id: 'session_ok2',
            first_user_message: 'plain text',
            resumable: true,
            diagnostics: [{ code: 'kept', message: 'ok' }],
          },
        ],
        next_cursor: null,
        extra: true,
      }),
    )
    const history = useSessionHistory({
      client: new AgentHttpClient({ fetchImpl }),
    })
    await flushPromises()

    expect(history.error.value).toBeNull()
    expect(history.items.value.map((item) => item.session_id)).toEqual([
      'session_fixture_1',
      'session_ok2',
    ])
    expect(history.items.value[0]?.diagnostics).toEqual([
      { code: 'incomplete_tail', message: 'history has an incomplete final record' },
    ])
    expect(history.items.value[0]?.first_user_message.text).toBe('请检查失败测试')
    expect(history.items.value[1]?.first_user_message).toMatchObject({
      text: 'plain text',
      truncated: false,
    })
    expect(history.items.value.every((item) => typeof item.session_id === 'string')).toBe(true)
  })

  it('does not persist the history list to localStorage', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const fetchImpl = vi.fn(async () => jsonResponse(fixture.history.list))
    const history = useSessionHistory({
      client: new AgentHttpClient({ fetchImpl }),
    })
    await flushPromises()
    await history.loadMore()

    expect(setItem).not.toHaveBeenCalled()
    setItem.mockRestore()
  })

  it('only calls listSessionHistory on the wire client', async () => {
    const listSessionHistory = vi.fn(async () => parseSessionHistoryPage(fixture.history.list))
    const createSession = vi.fn()
    const readSessionHistoryEvents = vi.fn()
    const resumeSession = vi.fn()
    const client = {
      listSessionHistory,
      createSession,
      readSessionHistoryEvents,
      resumeSession,
    } as unknown as AgentHttpClient
    const globalFetch = vi.spyOn(globalThis, 'fetch')

    const history = useSessionHistory({ client })
    await flushPromises()
    await history.refresh()

    expect(listSessionHistory).toHaveBeenCalled()
    expect(createSession).not.toHaveBeenCalled()
    expect(readSessionHistoryEvents).not.toHaveBeenCalled()
    expect(resumeSession).not.toHaveBeenCalled()
    expect(globalFetch).not.toHaveBeenCalled()
    globalFetch.mockRestore()
  })
})
