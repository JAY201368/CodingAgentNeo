import fixture from '../domain/fixtures/transport-v1.json'
import { describe, expect, it, vi } from 'vitest'

import { AgentHttpClient, AgentNetworkError } from '../api/client'
import { projectTimeline } from '../domain/timeline'
import { SessionCommandError, useAgentSession } from './useAgentSession'

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>()

  get length(): number {
    return this.values.size
  }

  clear(): void {
    this.values.clear()
  }

  getItem(key: string): string | null {
    return this.values.get(key) ?? null
  }

  key(index: number): string | null {
    return [...this.values.keys()][index] ?? null
  }

  removeItem(key: string): void {
    this.values.delete(key)
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value)
  }
}

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status })
}

function eventFrame(event: Record<string, unknown>): string {
  return `id: ${String(event.sequence)}\nevent: agent-event\ndata: ${JSON.stringify(event)}\n\n`
}

function streamResponse(events: readonly Record<string, unknown>[]): Response {
  return new Response(events.map(eventFrame).join(''), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

function openStreamResponse(): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(': keepalive\n\n'))
    },
    pull: () => new Promise<void>(() => undefined),
  })
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

function later(): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, 0))
}

const HISTORY_SESSION_ID = fixture.history.resume.request.resume_session_id
const RESUMED_TRANSPORT_ID = 'transport_resumed_1'

function historyTurnEvents(): Record<string, unknown>[] {
  return [
    {
      ...fixture.events[0],
      sequence: 1,
      type: 'session_start',
      payload: { state: 'RUNNING' },
    },
    {
      ...fixture.events[0],
      event_id: 'event_user_1',
      sequence: 2,
      type: 'user_message',
      payload: { text: 'inspect history' },
    },
    {
      ...fixture.events[1],
      sequence: 3,
      type: 'assistant_message',
      payload: { text: 'done' },
    },
    {
      ...fixture.events[3],
      sequence: 4,
      type: 'turn_end',
      payload: {
        state: 'COMPLETED_TURN',
        reason: 'complete',
        assistant_text: 'done',
        budget: {},
      },
    },
  ]
}

function historyPage(
  events: readonly Record<string, unknown>[],
  options: { hasMore?: boolean; nextCursor?: number | null } = {},
): Record<string, unknown> {
  const hasMore = options.hasMore === true
  return {
    session_id: HISTORY_SESSION_ID,
    events,
    next_cursor: hasMore ? (options.nextCursor ?? events[events.length - 1]?.sequence ?? null) : null,
    has_more: hasMore,
    diagnostics: [],
  }
}

function storedHint(storage: MemoryStorage): Record<string, unknown> | null {
  const raw = storage.getItem('coding-agent-neo.transport-session')
  if (raw === null) {
    return null
  }
  return JSON.parse(raw) as Record<string, unknown>
}

describe('useAgentSession', () => {
  it('persists only the opaque transport ID and successful cursor', async () => {
    const storage = new MemoryStorage()
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/sessions') && init?.method === 'POST') {
        return response({
          transport_session_id: fixture.session.transport_session_id,
          state: fixture.session.state,
          cursor: 0,
        }, 201)
      }
      return response({ accepted: true }, 202)
    })
    const session = useAgentSession({
      client: new AgentHttpClient({ fetchImpl }),
      storage,
      autoStartEvents: false,
    })

    await session.connect()
    const firstStored = storage.getItem('coding-agent-neo.transport-session')
    expect(firstStored).toBe(JSON.stringify({ transportSessionId: 'transport_fixture_1', cursor: 0 }))
    session.dispatch({ type: 'EVENT', event: fixture.events[0] })
    session.dispatch({ type: 'EVENT', event: fixture.events[1] })
    const stored = JSON.parse(storage.getItem('coding-agent-neo.transport-session') ?? '{}') as Record<string, unknown>
    expect(stored).toEqual({ transportSessionId: 'transport_fixture_1', cursor: 2 })
    expect(stored).not.toHaveProperty('payload')
    expect(stored).not.toHaveProperty('state')
    expect(stored).not.toHaveProperty('text')
  })

  it('does not send a second task while the first POST is unresolved', async () => {
    let resolveCommand: (() => void) | undefined
    const commandPromise = new Promise<Response>((resolve) => {
      resolveCommand = () => resolve(response({ accepted: true }, 202))
    })
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/sessions') && init?.method === 'POST') {
        return response({
          transport_session_id: 'transport_fixture_1',
          state: 'RUNNING',
          cursor: 0,
        }, 201)
      }
      return commandPromise
    })
    const session = useAgentSession({
      client: new AgentHttpClient({ fetchImpl }),
      storage: null,
      autoStartEvents: false,
    })
    await session.connect()
    const first = session.submitTask('inspect')
    expect(session.gate.value.kind).toBe('turn_running')
    await expect(session.submitTask('second')).rejects.toThrow('命令仍在提交')
    resolveCommand?.()
    await first
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('marks a network POST failure as uncertain and never retries it', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/sessions') && init?.method === 'POST') {
        return response({ transport_session_id: 'transport_fixture_1', state: 'RUNNING', cursor: 0 }, 201)
      }
      throw new AgentNetworkError('connection lost')
    })
    const session = useAgentSession({
      client: new AgentHttpClient({ fetchImpl }),
      storage: null,
      autoStartEvents: false,
    })
    await session.connect()
    await expect(session.submitTask('inspect')).rejects.toBeInstanceOf(AgentNetworkError)
    expect(session.state.value.commandUncertain).toBe(true)
    expect(session.gate.value.canSubmitTask).toBe(false)
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('delivers interrupt and close reasons once without replaying either POST', async () => {
    const bodies: string[] = []
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/sessions') && init?.method === 'POST') {
        return response({ transport_session_id: 'transport_fixture_1', state: 'RUNNING', cursor: 0 }, 201)
      }
      if (init?.body !== undefined) {
        bodies.push(String(init.body))
      }
      return response({ accepted: true }, 202)
    })
    const session = useAgentSession({
      client: new AgentHttpClient({ fetchImpl }),
      storage: null,
      autoStartEvents: false,
    })
    await session.connect()
    session.dispatch({
      type: 'EVENT',
      event: { ...fixture.events[0], sequence: 1, type: 'user_message', payload: { text: 'inspect' } },
    })
    await session.interrupt()
    await session.close()

    expect(bodies).toEqual([
      '{"type":"Interrupt","reason":"user_cancelled"}',
      '{"type":"CloseSession","reason":"frontend_exit"}',
    ])
    expect(fetchImpl).toHaveBeenCalledTimes(3)
  })

  it('locks approval after 202 and only releases it on the matching policy event', async () => {
    const bodies: string[] = []
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/sessions') && init?.method === 'POST') {
        return response({ transport_session_id: 'transport_fixture_1', state: 'RUNNING', cursor: 0 }, 201)
      }
      if (init?.body !== undefined) {
        bodies.push(String(init.body))
      }
      return response({ accepted: true }, 202)
    })
    const session = useAgentSession({
      client: new AgentHttpClient({ fetchImpl }),
      storage: null,
      autoStartEvents: false,
    })
    await session.connect()
    session.dispatch({
      type: 'EVENT',
      event: {
        ...fixture.events[0],
        sequence: 1,
        type: 'approval_request',
        correlation_id: 'correlation_fixture_1',
        payload: {
          request_id: 'correlation_fixture_1',
          tool_name: 'read_file',
          arguments_summary: 'safe summary',
        },
      },
    })

    await session.respondToApproval('correlation_fixture_1', true)
    expect(session.state.value.pendingApproval?.requestId).toBe('correlation_fixture_1')
    expect(session.state.value.commandInFlight).toBe('ApprovalResponse')
    await expect(session.respondToApproval('correlation_fixture_1', false))
      .rejects.toThrow('授权已提交')
    expect(bodies).toEqual([
      '{"type":"ApprovalResponse","request_id":"correlation_fixture_1","approved":true}',
    ])

    session.dispatch({
      type: 'EVENT',
      event: {
        ...fixture.events[1],
        sequence: 2,
        type: 'policy_decision',
        correlation_id: 'correlation_fixture_1',
        payload: { decision: 'allow' },
      },
    })
    expect(session.state.value.pendingApproval).toBeNull()
    expect(session.state.value.commandInFlight).toBeNull()
  })

  it('does not send approval for an invalid ID and keeps Stop available after stream loss', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/sessions') && init?.method === 'POST') {
        return response({ transport_session_id: 'transport_fixture_1', state: 'RUNNING', cursor: 0 }, 201)
      }
      return response({ accepted: true }, 202)
    })
    const session = useAgentSession({
      client: new AgentHttpClient({ fetchImpl }),
      storage: null,
      autoStartEvents: false,
    })
    await session.connect()
    session.dispatch({
      type: 'EVENT',
      event: {
        ...fixture.events[0],
        sequence: 1,
        type: 'approval_request',
        correlation_id: 'correlation_fixture_1',
        payload: { request_id: 'correlation_fixture_1' },
      },
    })
    await expect(session.respondToApproval('not-the-pending-id', true))
      .rejects.toThrow('授权请求 ID 无效')
    session.dispatch({ type: 'STREAM_ERROR', message: 'disconnected' })
    await expect(session.respondToApproval('correlation_fixture_1', true))
      .rejects.toThrow('事件流已断开')
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(session.gate.value.canInterrupt).toBe(true)
  })

  it('queries a persisted transport ID before reattaching from the browser cursor', async () => {
    const storage = new MemoryStorage()
    storage.setItem(
      'coding-agent-neo.transport-session',
      JSON.stringify({ transportSessionId: 'transport_resume', cursor: 2 }),
    )
    const calls: Array<{ path: string; method: string | undefined; headers: Headers }> = []
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      calls.push({ path, method: init?.method, headers: new Headers(init?.headers) })
      if (path.endsWith('/transport_resume') && init?.method === 'GET') {
        return response({ state: 'COMPLETED_TURN', cursor: 4, closed: false })
      }
      if (path.includes('/transport_resume/events')) {
        return streamResponse([
          { ...fixture.events[2], sequence: 3, type: 'assistant_message', payload: { text: 'reply' } },
          { ...fixture.events[3], sequence: 4, type: 'turn_end', payload: {
            state: 'COMPLETED_TURN', assistant_text: 'reply', reason: 'complete', budget: {},
          } },
        ])
      }
      if (path.endsWith('/commands')) {
        return response({ accepted: true }, 202)
      }
      throw new Error(`unexpected request: ${path}`)
    })
    const session = useAgentSession({
      client: new AgentHttpClient({ fetchImpl }),
      storage,
      reconnect: { maxAttempts: 1 },
    })

    await session.connect()
    await later()
    await later()

    expect(calls[0].path).toContain('/sessions/transport_resume')
    expect(calls[0].method).toBe('GET')
    expect(calls[1].path).toContain('/sessions/transport_resume/events?since=2')
    expect(calls[1].headers.get('Last-Event-ID')).toBe('2')
    expect(calls.some((call) => call.path.endsWith('/sessions') && call.method === 'POST')).toBe(false)
    expect(session.cursor.value).toBe(4)
    expect(session.state.value.events.map((event) => event.sequence)).toEqual([3, 4])
    expect(session.gate.value.canSubmitTask).toBe(true)

    await session.submitTask('follow-up')
    expect(calls.at(-1)?.path).toContain('/commands')
    expect((fetchImpl.mock.calls.at(-1)?.[1] as RequestInit).body)
      .toBe('{"type":"SubmitTask","text":"follow-up"}')
    session.stopEvents()
  })

  it('fails closed for an attached RUNNING snapshot until turn_end, then sends one follow-up', async () => {
    const storage = new MemoryStorage()
    storage.setItem(
      'coding-agent-neo.transport-session',
      JSON.stringify({ transportSessionId: 'transport_ambiguous', cursor: 0 }),
    )
    let commandPosts = 0
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path.endsWith('/transport_ambiguous') && init?.method === 'GET') {
        return response({ state: 'RUNNING', cursor: 6, closed: false })
      }
      if (path.includes('/transport_ambiguous/events')) {
        return openStreamResponse()
      }
      if (path.endsWith('/commands') && init?.method === 'POST') {
        commandPosts += 1
        return response({ accepted: true }, 202)
      }
      throw new Error(`unexpected request: ${path} ${String(init?.method)}`)
    })
    const session = useAgentSession({
      client: new AgentHttpClient({ fetchImpl }),
      storage,
      reconnect: { maxAttempts: 0 },
    })

    await session.connect()
    await later()
    await later()

    expect(session.state.value.resumeStateAmbiguous).toBe(true)
    expect(session.state.value.streamAvailable).toBe(true)
    expect(session.gate.value.canSubmitTask).toBe(false)
    expect(session.gate.value.reason).toContain('turn_end')
    await expect(session.submitTask('follow-up')).rejects.toThrow('turn_end')
    expect(commandPosts).toBe(0)

    session.dispatch({
      type: 'EVENT',
      event: {
        ...fixture.events[3],
        sequence: 1,
        type: 'turn_end',
        payload: { state: 'COMPLETED_TURN', assistant_text: 'reply' },
      },
    })
    expect(session.state.value.resumeStateAmbiguous).toBe(false)
    expect(session.gate.value.canSubmitTask).toBe(true)

    await session.submitTask('follow-up')
    expect(commandPosts).toBe(1)
    expect((fetchImpl.mock.calls.at(-1)?.[1] as RequestInit).body)
      .toBe('{"type":"SubmitTask","text":"follow-up"}')
    session.stopEvents()
  })

  it('clears a stale persisted ID after a 404 without creating a replacement automatically', async () => {
    const storage = new MemoryStorage()
    storage.setItem(
      'coding-agent-neo.transport-session',
      JSON.stringify({ transportSessionId: 'transport_gone', cursor: 8 }),
    )
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path.endsWith('/transport_gone')) {
        return response({ error: { code: 'session_not_found', message: 'private detail' } }, 404)
      }
      throw new Error(`unexpected request: ${path} ${String(init?.method)}`)
    })
    const session = useAgentSession({ client: new AgentHttpClient({ fetchImpl }), storage })

    await expect(session.connect()).rejects.toMatchObject({ code: 'session_not_found' })
    expect(storage.getItem('coding-agent-neo.transport-session')).toBeNull()
    expect(session.transportSessionId.value).toBeNull()
    expect(session.state.value.connection).toBe('error')
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })

  it('reattaches from the unchanged cursor after a sequence gap and keeps the gap diagnostic', async () => {
    let eventGets = 0
    const first = [
      { ...fixture.events[0], sequence: 1 },
      { ...fixture.events[1], sequence: 3 },
    ]
    const repaired = [
      { ...fixture.events[1], sequence: 2 },
      { ...fixture.events[2], sequence: 3 },
    ]
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path.endsWith('/sessions') && init?.method === 'POST') {
        return response({
          transport_session_id: 'transport_gap', state: 'RUNNING', cursor: 0,
        }, 201)
      }
      if (path.includes('/events')) {
        eventGets += 1
        return streamResponse(eventGets === 1 ? first : repaired)
      }
      throw new Error(`unexpected request: ${path}`)
    })
    const session = useAgentSession({
      client: new AgentHttpClient({ fetchImpl }),
      storage: null,
      reconnect: { initialDelayMs: 100, maxAttempts: 1 },
    })

    await session.connect()
    await later()
    await later()
    await later()

    expect(eventGets).toBeGreaterThanOrEqual(2)
    expect((fetchImpl.mock.calls[1]?.[0] as string)).toContain('/events?since=0')
    expect((fetchImpl.mock.calls[2]?.[0] as string)).toContain('/events?since=1')
    expect(session.cursor.value).toBe(3)
    expect(session.state.value.events.map((event) => event.sequence)).toEqual([1, 2, 3])
    expect(session.state.value.needsResubscribe).toBe(false)
    expect(session.state.value.diagnostics).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'sequence_gap', expected: 2, sequence: 3 }),
    ]))
    session.stopEvents()
  })

  it('uses finite backoff for failed SSE GETs and never retries a POST', async () => {
    let eventGets = 0
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path.endsWith('/sessions') && init?.method === 'POST') {
        return response({
          transport_session_id: 'transport_retry', state: 'RUNNING', cursor: 0,
        }, 201)
      }
      if (path.includes('/events')) {
        eventGets += 1
        throw new AgentNetworkError('connection lost')
      }
      throw new Error(`unexpected request: ${path}`)
    })
    const session = useAgentSession({
      client: new AgentHttpClient({ fetchImpl }),
      storage: null,
      reconnect: { initialDelayMs: 0, maxDelayMs: 0, maxAttempts: 2 },
    })

    await session.connect()
    await later()
    await later()
    await later()
    await later()

    expect(eventGets).toBe(3)
    expect(session.state.value.streamRetryExhausted).toBe(true)
    expect(fetchImpl.mock.calls.filter((call) => (call[1] as RequestInit | undefined)?.method === 'POST'))
      .toHaveLength(1)
    session.stopEvents()
  })

  describe('resumeSession', () => {
    it('terminates, resets, resumes, hydrates in sequence, then attaches SSE from the reducer cursor', async () => {
      const storage = new MemoryStorage()
      const events = historyTurnEvents()
      const calls: Array<{ method: string; path: string; body: unknown }> = []
      const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        const method = init?.method ?? 'GET'
        const body = typeof init?.body === 'string' ? JSON.parse(init.body) as unknown : null
        calls.push({ method, path, body })
        if (path.endsWith('/sessions') && method === 'POST') {
          if (body !== null && typeof body === 'object' && 'resume_session_id' in body) {
            expect(body).toEqual({ resume_session_id: HISTORY_SESSION_ID })
            return response({
              transport_session_id: RESUMED_TRANSPORT_ID,
              state: fixture.history.resume.response.state,
              cursor: fixture.history.resume.response.cursor,
            }, 201)
          }
          return response({
            transport_session_id: 'transport_live',
            state: 'RUNNING',
            cursor: 0,
          }, 201)
        }
        if (path.endsWith('/sessions/transport_live') && method === 'DELETE') {
          return new Response(null, { status: 204 })
        }
        if (path.includes('/session-history/') && path.includes('/events')) {
          expect(path).toContain(`/session-history/${HISTORY_SESSION_ID}/events`)
          expect(path).toContain('since=0')
          return response(historyPage(events))
        }
        if (path.includes(`/${RESUMED_TRANSPORT_ID}/events`)) {
          return openStreamResponse()
        }
        if (path.endsWith('/commands') && method === 'POST') {
          return response({ accepted: true }, 202)
        }
        throw new Error(`unexpected request: ${method} ${path}`)
      })
      const session = useAgentSession({
        client: new AgentHttpClient({ fetchImpl }),
        storage,
        autoStartEvents: false,
        reconnect: { maxAttempts: 0 },
      })

      await session.connect()
      session.dispatch({
        type: 'EVENT',
        event: { ...fixture.events[0], sequence: 1, type: 'user_message', payload: { text: 'old live turn' } },
      })
      expect(session.state.value.events).toHaveLength(1)

      await session.resumeSession(HISTORY_SESSION_ID)
      await later()
      await later()

      expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
        'POST /api/v1/sessions',
        'DELETE /api/v1/sessions/transport_live',
        'POST /api/v1/sessions',
        `GET /api/v1/session-history/${HISTORY_SESSION_ID}/events?since=0`,
        `GET /api/v1/sessions/${RESUMED_TRANSPORT_ID}/events?since=4`,
      ])
      expect(calls[2].body).toEqual({ resume_session_id: HISTORY_SESSION_ID })
      expect(session.transportSessionId.value).toBe(RESUMED_TRANSPORT_ID)
      expect(session.cursor.value).toBe(4)
      expect(session.cursor.value).toBe(fixture.history.resume.response.cursor)
      expect(session.state.value.events.map((event) => event.sequence)).toEqual([1, 2, 3, 4])
      expect(session.state.value.events.some((event) => event.type === 'user_message' && event.payload.text === 'old live turn'))
        .toBe(false)
      expect(session.state.value.finalAssistantText).toBe('done')
      expect(session.state.value.status).toBe('COMPLETED_TURN')
      const timeline = projectTimeline(session.state.value.events)
      expect(timeline.map((item) => item.kind)).toEqual(expect.arrayContaining(['user', 'assistant', 'end']))
      expect(timeline.some((item) => item.kind === 'user' && item.text.includes('inspect history'))).toBe(true)
      const stored = storedHint(storage)
      expect(stored).toEqual({ transportSessionId: RESUMED_TRANSPORT_ID, cursor: 4 })
      expect(stored).not.toHaveProperty('historySessionId')
      expect(JSON.stringify(stored)).not.toContain(HISTORY_SESSION_ID)
      expect(session.switching.value).toBe(false)

      await session.submitTask('follow-up')
      expect(calls.at(-1)?.path).toContain(`/sessions/${RESUMED_TRANSPORT_ID}/commands`)
      expect(calls.at(-1)?.body).toEqual({ type: 'SubmitTask', text: 'follow-up' })
      session.stopEvents()
    })

    it('pages history with next_cursor as the next since until has_more is false', async () => {
      const events = historyTurnEvents()
      const sinceValues: number[] = []
      const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        if (path.endsWith('/sessions') && init?.method === 'POST') {
          return response({
            transport_session_id: RESUMED_TRANSPORT_ID,
            state: 'RUNNING',
            cursor: 4,
          }, 201)
        }
        if (path.includes('/session-history/') && path.includes('/events')) {
          const since = Number(new URL(path, 'http://local.invalid').searchParams.get('since'))
          sinceValues.push(since)
          if (since === 0) {
            return response(historyPage(events.slice(0, 2), { hasMore: true, nextCursor: 2 }))
          }
          if (since === 2) {
            return response(historyPage(events.slice(2)))
          }
          throw new Error(`unexpected since=${since}`)
        }
        if (path.includes(`/${RESUMED_TRANSPORT_ID}/events`)) {
          return openStreamResponse()
        }
        throw new Error(`unexpected request: ${path}`)
      })
      const session = useAgentSession({
        client: new AgentHttpClient({ fetchImpl }),
        storage: null,
        reconnect: { maxAttempts: 0 },
      })

      await session.resumeSession(HISTORY_SESSION_ID)
      await later()

      expect(sinceValues).toEqual([0, 2])
      expect(session.cursor.value).toBe(4)
      expect(session.state.value.events.map((event) => event.sequence)).toEqual([1, 2, 3, 4])
      expect(String(fetchImpl.mock.calls.at(-1)?.[0])).toContain(`/events?since=4`)
      session.stopEvents()
    })

    it('ignores duplicate sequences and keeps a gap diagnostic without crashing', async () => {
      const events = historyTurnEvents()
      const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        if (path.endsWith('/sessions') && init?.method === 'POST') {
          return response({
            transport_session_id: RESUMED_TRANSPORT_ID,
            state: 'RUNNING',
            cursor: 4,
          }, 201)
        }
        if (path.includes('/session-history/') && path.includes('/events')) {
          return response(historyPage([
            events[0],
            events[1],
            events[1],
            events[3],
          ]))
        }
        if (path.includes(`/${RESUMED_TRANSPORT_ID}/events`)) {
          return openStreamResponse()
        }
        throw new Error(`unexpected request: ${path}`)
      })
      const session = useAgentSession({
        client: new AgentHttpClient({ fetchImpl }),
        storage: null,
        reconnect: { maxAttempts: 0 },
      })

      await session.resumeSession(HISTORY_SESSION_ID)
      await later()

      expect(session.state.value.events.map((event) => event.sequence)).toEqual([1, 2])
      expect(session.state.value.events.filter((event) => event.sequence === 2)).toHaveLength(1)
      expect(session.cursor.value).toBe(2)
      expect(session.state.value.diagnostics).toEqual(expect.arrayContaining([
        expect.objectContaining({ code: 'sequence_gap', expected: 3, sequence: 4 }),
      ]))
      session.stopEvents()
    })

    it('retains unknown and truncated history events without crashing', async () => {
      const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        if (path.endsWith('/sessions') && init?.method === 'POST') {
          return response({
            transport_session_id: RESUMED_TRANSPORT_ID,
            state: 'RUNNING',
            cursor: 3,
          }, 201)
        }
        if (path.includes('/session-history/')) {
          return response(historyPage([
            historyTurnEvents()[0],
            {
              ...fixture.events[2],
              sequence: 2,
            },
            {
              ...fixture.events[1],
              sequence: 3,
              payload: fixture.history.truncated_payload,
            },
          ]))
        }
        if (path.includes('/events')) {
          return openStreamResponse()
        }
        throw new Error(`unexpected request: ${path}`)
      })
      const session = useAgentSession({
        client: new AgentHttpClient({ fetchImpl }),
        storage: null,
        reconnect: { maxAttempts: 0 },
      })

      await session.resumeSession(HISTORY_SESSION_ID)
      await later()

      expect(session.cursor.value).toBe(3)
      expect(session.state.value.events.map((event) => event.sequence)).toEqual([1, 2, 3])
      expect(session.state.value.diagnostics).toEqual(expect.arrayContaining([
        expect.objectContaining({ code: 'unknown_event_type' }),
        expect.objectContaining({ code: 'truncated_payload' }),
      ]))
      session.stopEvents()
    })

    it('skips DELETE when there is no current transport session', async () => {
      const methods: string[] = []
      const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        const method = init?.method ?? 'GET'
        methods.push(method)
        if (path.endsWith('/sessions') && method === 'POST') {
          return response({
            transport_session_id: RESUMED_TRANSPORT_ID,
            state: 'RUNNING',
            cursor: 4,
          }, 201)
        }
        if (path.includes('/session-history/')) {
          return response(historyPage(historyTurnEvents()))
        }
        if (path.includes('/events')) {
          return openStreamResponse()
        }
        throw new Error(`unexpected request: ${method} ${path}`)
      })
      const session = useAgentSession({
        client: new AgentHttpClient({ fetchImpl }),
        storage: null,
        reconnect: { maxAttempts: 0 },
      })

      await session.resumeSession(HISTORY_SESSION_ID)
      await later()

      expect(methods).not.toContain('DELETE')
      expect(session.transportSessionId.value).toBe(RESUMED_TRANSPORT_ID)
      session.stopEvents()
    })

    it.each([404, 410])('treats DELETE %s as already closed and continues resume', async (status) => {
      const methods: string[] = []
      const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        const method = init?.method ?? 'GET'
        methods.push(`${method} ${path}`)
        if (path.endsWith('/sessions') && method === 'POST') {
          const body = typeof init?.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : {}
          if (body.resume_session_id === HISTORY_SESSION_ID) {
            return response({
              transport_session_id: RESUMED_TRANSPORT_ID,
              state: 'RUNNING',
              cursor: 4,
            }, 201)
          }
          return response({
            transport_session_id: 'transport_stale',
            state: 'RUNNING',
            cursor: 0,
          }, 201)
        }
        if (path.endsWith('/sessions/transport_stale') && method === 'DELETE') {
          return response({ error: { code: status === 410 ? 'session_closed' : 'session_not_found', message: 'private' } }, status)
        }
        if (path.includes('/session-history/')) {
          return response(historyPage(historyTurnEvents()))
        }
        if (path.includes(`/${RESUMED_TRANSPORT_ID}/events`)) {
          return openStreamResponse()
        }
        throw new Error(`unexpected request: ${method} ${path}`)
      })
      const session = useAgentSession({
        client: new AgentHttpClient({ fetchImpl }),
        storage: null,
        autoStartEvents: false,
        reconnect: { maxAttempts: 0 },
      })

      await session.connect()
      await session.resumeSession(HISTORY_SESSION_ID)
      await later()

      expect(methods.some((line) => line.startsWith('DELETE '))).toBe(true)
      expect(methods.some((line) => line === 'POST /api/v1/sessions')).toBe(true)
      expect(session.transportSessionId.value).toBe(RESUMED_TRANSPORT_ID)
      expect(session.cursor.value).toBe(4)
      session.stopEvents()
    })

    it('rejects re-entry while switching and releases the lock afterwards', async () => {
      let releaseDelete: (() => void) | undefined
      const deleteGate = new Promise<void>((resolve) => {
        releaseDelete = resolve
      })
      const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        const method = init?.method ?? 'GET'
        if (path.endsWith('/sessions') && method === 'POST') {
          const body = typeof init?.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : {}
          if (body.resume_session_id === HISTORY_SESSION_ID) {
            return response({
              transport_session_id: RESUMED_TRANSPORT_ID,
              state: 'RUNNING',
              cursor: 4,
            }, 201)
          }
          return response({
            transport_session_id: 'transport_live',
            state: 'RUNNING',
            cursor: 0,
          }, 201)
        }
        if (method === 'DELETE') {
          await deleteGate
          return new Response(null, { status: 204 })
        }
        if (path.includes('/session-history/')) {
          return response(historyPage(historyTurnEvents()))
        }
        if (path.includes('/events')) {
          return openStreamResponse()
        }
        throw new Error(`unexpected request: ${method} ${path}`)
      })
      const session = useAgentSession({
        client: new AgentHttpClient({ fetchImpl }),
        storage: null,
        autoStartEvents: false,
        reconnect: { maxAttempts: 0 },
      })
      await session.connect()

      const first = session.resumeSession(HISTORY_SESSION_ID)
      await later()
      expect(session.switching.value).toBe(true)
      await expect(session.resumeSession(HISTORY_SESSION_ID)).rejects.toBeInstanceOf(SessionCommandError)
      releaseDelete?.()
      await first
      await later()
      expect(session.switching.value).toBe(false)
      session.stopEvents()
    })

    it.each([
      { status: 404, code: 'history_not_found', message: 'session history was not found' },
      { status: 422, code: 'history_unavailable', message: 'session history is unavailable' },
      { status: 422, code: 'invalid_resume', message: 'session cannot be resumed' },
      { status: 409, code: 'session_exists', message: 'an active transport session already exists' },
    ])('fail-closes after DELETE when resume create returns $status $code', async ({ status, code, message }) => {
      const storage = new MemoryStorage()
      const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        const method = init?.method ?? 'GET'
        if (path.endsWith('/sessions') && method === 'POST') {
          const body = typeof init?.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : {}
          if (body.resume_session_id === HISTORY_SESSION_ID) {
            return response({ error: { code, message: 'private backend detail' } }, status)
          }
          return response({
            transport_session_id: 'transport_live',
            state: 'RUNNING',
            cursor: 0,
          }, 201)
        }
        if (method === 'DELETE') {
          return new Response(null, { status: 204 })
        }
        throw new Error(`unexpected request: ${method} ${path}`)
      })
      const session = useAgentSession({
        client: new AgentHttpClient({ fetchImpl }),
        storage,
        autoStartEvents: false,
      })
      await session.connect()
      expect(storage.getItem('coding-agent-neo.transport-session')).not.toBeNull()

      await expect(session.resumeSession(HISTORY_SESSION_ID)).rejects.toMatchObject({ code, status })

      const postCalls = fetchImpl.mock.calls.filter((call) => (call[1] as RequestInit | undefined)?.method === 'POST')
      expect(postCalls).toHaveLength(2)
      expect(fetchImpl.mock.calls.some((call) => String(call[0]).includes('/session-history/'))).toBe(false)
      expect(session.transportSessionId.value).toBeNull()
      expect(session.state.value.connection).toBe('error')
      expect(session.state.value.lastError).toBe(message)
      expect(session.state.value.lastError).not.toContain('private')
      expect(storage.getItem('coding-agent-neo.transport-session')).toBeNull()
      expect(session.switching.value).toBe(false)
    })

    it('does not POST resume when DELETE cannot prove the current session closed', async () => {
      const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        const method = init?.method ?? 'GET'
        if (path.endsWith('/sessions') && method === 'POST') {
          return response({
            transport_session_id: 'transport_live',
            state: 'RUNNING',
            cursor: 0,
          }, 201)
        }
        if (method === 'DELETE') {
          throw new AgentNetworkError('connection lost')
        }
        throw new Error(`unexpected request: ${method} ${path}`)
      })
      const session = useAgentSession({
        client: new AgentHttpClient({ fetchImpl }),
        storage: null,
        autoStartEvents: false,
      })
      await session.connect()

      await expect(session.resumeSession(HISTORY_SESSION_ID)).rejects.toBeInstanceOf(AgentNetworkError)
      expect(fetchImpl.mock.calls.filter((call) => (call[1] as RequestInit | undefined)?.method === 'POST'))
        .toHaveLength(1)
      expect(session.transportSessionId.value).toBe('transport_live')
      expect(session.state.value.lastError).toBe('connection lost')
      expect(session.switching.value).toBe(false)
    })

    it('does not replay POST after a resume create network failure', async () => {
      const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        const method = init?.method ?? 'GET'
        const body = typeof init?.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : {}
        if (path.endsWith('/sessions') && method === 'POST') {
          if (body.resume_session_id === HISTORY_SESSION_ID) {
            throw new AgentNetworkError('connection lost')
          }
          return response({
            transport_session_id: 'transport_live',
            state: 'RUNNING',
            cursor: 0,
          }, 201)
        }
        if (method === 'DELETE') {
          return new Response(null, { status: 204 })
        }
        throw new Error(`unexpected request: ${method} ${path}`)
      })
      const session = useAgentSession({
        client: new AgentHttpClient({ fetchImpl }),
        storage: null,
        autoStartEvents: false,
      })
      await session.connect()

      await expect(session.resumeSession(HISTORY_SESSION_ID)).rejects.toBeInstanceOf(AgentNetworkError)
      const resumePosts = fetchImpl.mock.calls.filter((call) => {
        const path = String(call[0])
        const init = call[1] as RequestInit | undefined
        return path.endsWith('/sessions') && init?.method === 'POST' && String(init.body).includes('resume_session_id')
      })
      expect(resumePosts).toHaveLength(1)
      expect(session.transportSessionId.value).toBeNull()
      expect(session.state.value.connection).toBe('error')
    })

    it.each(['../x', 'transport_fixture_1', 'session_foo.jsonl'])(
      'rejects illegal history id %s before POST or history GET',
      async (illegalId) => {
        const fetchImpl = vi.fn(async () => {
          throw new Error('network must not be used')
        })
        const session = useAgentSession({
          client: new AgentHttpClient({ fetchImpl }),
          storage: null,
          autoStartEvents: false,
        })

        await expect(session.resumeSession(illegalId)).rejects.toMatchObject({
          code: 'invalid_history_id',
          status: 400,
        })
        expect(fetchImpl).not.toHaveBeenCalled()
        expect(session.transportSessionId.value).toBeNull()
        expect(session.switching.value).toBe(false)
      },
    )

    it('does not DELETE the current session when the history id is illegal', async () => {
      const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        if (path.endsWith('/sessions') && init?.method === 'POST') {
          return response({
            transport_session_id: 'transport_live',
            state: 'RUNNING',
            cursor: 0,
          }, 201)
        }
        throw new Error(`unexpected request: ${path}`)
      })
      const session = useAgentSession({
        client: new AgentHttpClient({ fetchImpl }),
        storage: null,
        autoStartEvents: false,
      })
      await session.connect()
      await expect(session.resumeSession('transport_live')).rejects.toMatchObject({
        code: 'invalid_history_id',
      })
      expect(fetchImpl.mock.calls.filter((call) => (call[1] as RequestInit | undefined)?.method === 'DELETE'))
        .toHaveLength(0)
      expect(session.transportSessionId.value).toBe('transport_live')
    })

    it('does not send a history id on transport routes or a transport id on history routes', async () => {
      const paths: string[] = []
      const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        paths.push(path)
        const method = init?.method ?? 'GET'
        if (path.endsWith('/sessions') && method === 'POST') {
          const body = typeof init?.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : {}
          if (body.resume_session_id === HISTORY_SESSION_ID) {
            return response({
              transport_session_id: RESUMED_TRANSPORT_ID,
              state: 'RUNNING',
              cursor: 4,
            }, 201)
          }
          return response({
            transport_session_id: 'transport_live',
            state: 'RUNNING',
            cursor: 0,
          }, 201)
        }
        if (method === 'DELETE') {
          expect(path).toContain('/sessions/transport_live')
          expect(path).not.toContain(HISTORY_SESSION_ID)
          return new Response(null, { status: 204 })
        }
        if (path.includes('/session-history/')) {
          expect(path).toContain(`/session-history/${HISTORY_SESSION_ID}/`)
          expect(path).not.toContain('transport_')
          return response(historyPage(historyTurnEvents()))
        }
        if (path.includes('/events')) {
          expect(path).toContain(`/sessions/${RESUMED_TRANSPORT_ID}/events`)
          expect(path).not.toContain(HISTORY_SESSION_ID)
          return openStreamResponse()
        }
        throw new Error(`unexpected request: ${method} ${path}`)
      })
      const session = useAgentSession({
        client: new AgentHttpClient({ fetchImpl }),
        storage: null,
        autoStartEvents: false,
        reconnect: { maxAttempts: 0 },
      })
      await session.connect()
      await session.resumeSession(HISTORY_SESSION_ID)
      await later()

      expect(paths.some((path) => path.includes(`/sessions/${HISTORY_SESSION_ID}`))).toBe(false)
      expect(paths.some((path) => path.includes('/session-history/transport_'))).toBe(false)
      session.stopEvents()
    })
  })
})
