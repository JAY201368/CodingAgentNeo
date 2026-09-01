import fixture from '../domain/fixtures/transport-v1.json'
import { describe, expect, it, vi } from 'vitest'

import { AgentHttpClient, AgentNetworkError } from '../api/client'
import { useAgentSession } from './useAgentSession'

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
})
