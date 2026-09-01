import fixture from '../domain/fixtures/transport-v1.json'
import {
  AgentApiError,
  AgentHttpClient,
  AgentNetworkError,
  parseSseStream,
} from './client'
import type { AgentCommand } from '../domain/protocol'
import { describe, expect, it, vi } from 'vitest'

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('AgentHttpClient', () => {
  it('uses the v1 paths, exact wire bodies, and validates protocol version', async () => {
    const submitTask = { type: 'SubmitTask' as const, text: 'inspect' }
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = []
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ input, init })
      const path = String(input)
      if (path.endsWith('/health')) {
        return jsonResponse(fixture.health)
      }
      if (path.endsWith('/sessions') && init?.method === 'POST') {
        return jsonResponse({
          transport_session_id: fixture.session.transport_session_id,
          state: fixture.session.state,
          cursor: fixture.session.cursor,
        }, 201)
      }
      if (path.endsWith('/commands')) {
        return jsonResponse({ accepted: true }, 202)
      }
      if (init?.method === 'DELETE') {
        return new Response(null, { status: 204 })
      }
      if (path.includes('/sessions/transport_fixture_1')) {
        return jsonResponse(fixture.session)
      }
      return new Response(null, { status: 204 })
    })
    const client = new AgentHttpClient({ baseUrl: 'http://127.0.0.1:8765/', fetchImpl })

    await expect(client.health()).resolves.toEqual(fixture.health)
    await expect(client.createSession()).resolves.toMatchObject({
      transport_session_id: fixture.session.transport_session_id,
      cursor: 0,
    })
    await expect(
      client.sendCommand('transport_fixture_1', submitTask),
    ).resolves.toEqual({ accepted: true })
    await expect(client.getSession('transport_fixture_1')).resolves.toMatchObject({ cursor: 0 })
    await expect(client.deleteSession('transport_fixture_1')).resolves.toBeUndefined()

    expect(calls[0].input).toBe('http://127.0.0.1:8765/api/v1/health')
    expect(calls[1].input).toBe('http://127.0.0.1:8765/api/v1/sessions')
    expect(calls[1].init?.method).toBe('POST')
    expect(calls[1].init?.body).toBe('{}')
    expect(calls[2].input).toBe('http://127.0.0.1:8765/api/v1/sessions/transport_fixture_1/commands')
    expect(calls[2].init?.method).toBe('POST')
    expect(calls[2].init?.body).toBe(JSON.stringify(submitTask))
    expect(calls[4].init?.method).toBe('DELETE')
  })

  it('sends both cursor forms and yields canonical SSE data without POST replay', async () => {
    const submitTask = { type: 'SubmitTask' as const, text: 'inspect' }
    const sse = fixture.events
      .slice(0, 2)
      .map((event) => `id: ${event.sequence}\nevent: agent-event\ndata: ${JSON.stringify(event)}\n\n`)
      .join('')
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') {
        throw new TypeError('connection lost after send')
      }
      return new Response(sse, { headers: { 'Content-Type': 'text/event-stream' } })
    })
    const client = new AgentHttpClient({ fetchImpl })

    const messages = []
    for await (const message of client.events('transport_fixture_1', 4)) {
      messages.push(message)
    }
    expect(messages).toHaveLength(2)
    expect(messages[0].id).toBe('1')
    expect(messages[0].data).toEqual(fixture.events[0])
    const sseInit = fetchImpl.mock.calls[0][1] as RequestInit
    expect(String(fetchImpl.mock.calls[0][0])).toContain('/api/v1/sessions/transport_fixture_1/events?since=4')
    expect(new Headers(sseInit.headers).get('Last-Event-ID')).toBe('4')
    expect(new Headers(sseInit.headers).get('Accept')).toBe('text/event-stream')

    await expect(
      client.sendCommand('transport_fixture_1', submitTask),
    ).rejects.toBeInstanceOf(AgentNetworkError)
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('sends every canonical fixture command with exact JSON, including optional reasons', async () => {
    const bodies: string[] = []
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.body !== undefined) {
        bodies.push(String(init.body))
      }
      return jsonResponse({ accepted: true }, 202)
    })
    const client = new AgentHttpClient({ fetchImpl })

    for (const command of fixture.commands) {
      await client.sendCommand('transport_fixture_1', command as AgentCommand)
    }

    expect(bodies).toEqual(fixture.commands.map((command) => JSON.stringify(command)))
    expect(bodies).toEqual([
      '{"type":"SubmitTask","text":"inspect"}',
      '{"type":"ApprovalResponse","request_id":"correlation_fixture_1","approved":false}',
      '{"type":"Interrupt","reason":"user_cancelled"}',
      '{"type":"CloseSession","reason":"frontend_exit"}',
    ])
  })

  it('allows Interrupt and CloseSession without a reason but rejects extras, empty reasons, and wrong types', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input
      void init
      return jsonResponse({ accepted: true }, 202)
    })
    const client = new AgentHttpClient({ fetchImpl })

    await client.sendCommand('transport_fixture_1', { type: 'Interrupt' })
    await client.sendCommand('transport_fixture_1', { type: 'CloseSession' })
    expect(fetchImpl).toHaveBeenCalledTimes(2)
    expect((fetchImpl.mock.calls[0][1] as RequestInit).body).toBe('{"type":"Interrupt"}')
    expect((fetchImpl.mock.calls[1][1] as RequestInit).body).toBe('{"type":"CloseSession"}')

    const invalidCommands = [
      { type: 'Interrupt', reason: '' },
      { type: 'CloseSession', reason: 42 },
      { type: 'Interrupt', reason: 'user_cancelled', extra: true },
      { type: 'CloseSession', reason: 'frontend_exit', extra: true },
    ]
    for (const command of invalidCommands) {
      await expect(
        client.sendCommand('transport_fixture_1', command as never),
      ).rejects.toMatchObject({ status: 400, code: 'invalid_command' })
    }
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('normalizes stable server errors and rejects malformed commands before fetch', async () => {
    const submitTask = { type: 'SubmitTask' as const, text: 'inspect' }
    const fetchImpl = vi.fn(async () => jsonResponse({ error: fixture.errors[0] }, 409))
    const client = new AgentHttpClient({ fetchImpl })
    await expect(
      client.sendCommand('transport_fixture_1', submitTask),
    ).rejects.toMatchObject({ status: 409, code: 'turn_in_progress' })

    const errorClient = new AgentHttpClient({ fetchImpl: vi.fn() })
    await expect(
      errorClient.sendCommand('transport_fixture_1', {
        type: 'SubmitTask',
        text: 'inspect',
        extra: true,
      } as never),
    ).rejects.toMatchObject({ status: 400, code: 'invalid_command' })
    expect(errorClient).toBeDefined()
  })

  it('parses comments, CRLF, multiline data, and malformed data safely', async () => {
    const body = new Response(
      ': keepalive\r\n\r\nid: 7\r\nevent: agent-event\r\ndata: {"a":\r\ndata: 1}\r\n\r\nid: 8\r\nevent: agent-event\r\ndata: truncated\r\n\r\n',
    ).body
    if (body === null) {
      throw new Error('test response did not create a body')
    }
    const messages = []
    for await (const message of parseSseStream(body)) {
      messages.push(message)
    }
    expect(messages[0].data).toEqual({ a: 1 })
    expect(messages[1].data).toBeNull()
    expect(messages[1].rawData).toBe('truncated')
  })

  it('surfaces protocol errors without exposing arbitrary response text', async () => {
    const client = new AgentHttpClient({
      fetchImpl: vi.fn(async () => jsonResponse({ status: 'ok', protocol_version: 99 })),
    })
    await expect(client.health()).rejects.toBeInstanceOf(AgentApiError)
  })
})

describe('AgentHttpClient history and resume', () => {
  it('lists history with default and explicit query params without decoding cursors', async () => {
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = []
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ input, init })
      return jsonResponse(fixture.history.list)
    })
    const client = new AgentHttpClient({ baseUrl: 'http://127.0.0.1:8765', fetchImpl })

    await expect(client.listSessionHistory()).resolves.toMatchObject({
      sessions: [{ session_id: 'session_fixture_1', resumable: true }],
      next_cursor: 'opaque_history_cursor_fixture_1',
    })
    await expect(
      client.listSessionHistory({
        limit: 50,
        cursor: fixture.history.list.next_cursor,
      }),
    ).resolves.toMatchObject({ next_cursor: 'opaque_history_cursor_fixture_1' })
    const injected = await client.listSessionHistory({ cursor: 'offset=10&path=/secret' })
    expect(injected.sessions[0]?.session_id).toBe('session_fixture_1')

    expect(String(calls[0].input)).toBe('http://127.0.0.1:8765/api/v1/session-history')
    expect(calls[0].init?.method).toBe('GET')
    expect(String(calls[1].input)).toBe(
      'http://127.0.0.1:8765/api/v1/session-history?limit=50&cursor=opaque_history_cursor_fixture_1',
    )
    expect(String(calls[2].input)).toBe(
      `http://127.0.0.1:8765/api/v1/session-history?cursor=${encodeURIComponent('offset=10&path=/secret')}`,
    )
    expect(String(calls[2].input)).not.toContain('path=/secret')
  })

  it('rejects illegal list limit and cursor before fetch', async () => {
    const fetchImpl = vi.fn()
    const client = new AgentHttpClient({ fetchImpl })
    await expect(client.listSessionHistory({ limit: 0 })).rejects.toMatchObject({
      status: 400,
      code: 'invalid_history_limit',
      message: 'history limit is invalid',
    })
    await expect(client.listSessionHistory({ limit: 101 })).rejects.toMatchObject({
      status: 400,
      code: 'invalid_history_limit',
    })
    await expect(client.listSessionHistory({ cursor: '' })).rejects.toMatchObject({
      status: 400,
      code: 'invalid_history_cursor',
      message: 'history cursor is invalid',
    })
    await expect(client.listSessionHistory({ cursor: 'x'.repeat(257) })).rejects.toMatchObject({
      status: 400,
      code: 'invalid_history_cursor',
    })
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('reads history events with session_ token checks and since/limit bounds', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path.includes('events_empty')) {
        return jsonResponse(fixture.history.events_empty)
      }
      return jsonResponse(fixture.history.events)
    })
    const client = new AgentHttpClient({ baseUrl: 'http://127.0.0.1:8765', fetchImpl })
    const page = await client.readSessionHistoryEvents('session_fixture_1', {
      since: 0,
      limit: 200,
    })
    expect(page.events.map((event) => event.sequence)).toEqual([1, 2])
    expect(page.has_more).toBe(true)
    expect(page.next_cursor).toBe(2)
    expect(String(fetchImpl.mock.calls[0]?.[0])).toBe(
      'http://127.0.0.1:8765/api/v1/session-history/session_fixture_1/events?since=0&limit=200',
    )

    await expect(client.readSessionHistoryEvents('session_fixture_1')).resolves.toMatchObject({
      session_id: 'session_fixture_1',
    })
    expect(String(fetchImpl.mock.calls[1]?.[0])).toBe(
      'http://127.0.0.1:8765/api/v1/session-history/session_fixture_1/events',
    )
  })

  it('rejects unsafe history IDs, since, and event limits before fetch', async () => {
    const fetchImpl = vi.fn()
    const client = new AgentHttpClient({ fetchImpl })
    const invalidIds = [
      'transport_fixture_1',
      '../outside',
      'session_foo/bar',
      'session_foo\\bar',
      'session_foo.',
      'session_foo.jsonl',
      'session_foo\0bar',
      '/absolute/session_x',
    ]
    for (const sessionId of invalidIds) {
      await expect(client.readSessionHistoryEvents(sessionId)).rejects.toMatchObject({
        status: 400,
        code: 'invalid_history_id',
        message: 'history session ID is invalid',
      })
    }
    await expect(
      client.readSessionHistoryEvents('session_fixture_1', { since: -1 }),
    ).rejects.toMatchObject({ status: 400, code: 'invalid_history_cursor' })
    await expect(
      client.readSessionHistoryEvents('session_fixture_1', { limit: 201 }),
    ).rejects.toMatchObject({ status: 400, code: 'invalid_history_limit' })
    await expect(
      client.createSession('../outside'),
    ).rejects.toMatchObject({ status: 400, code: 'invalid_history_id' })
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('sends {} by default, resume_session_id only when resuming, and never replays POST', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') {
        throw new TypeError('connection lost after send')
      }
      void input
      return jsonResponse(fixture.history.list)
    })
    const client = new AgentHttpClient({ fetchImpl })
    await expect(client.createSession()).rejects.toBeInstanceOf(AgentNetworkError)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect((fetchImpl.mock.calls[0]?.[1] as RequestInit).body).toBe('{}')

    const abort = new AbortController()
    await expect(client.createSession(abort.signal)).rejects.toBeInstanceOf(AgentNetworkError)
    expect((fetchImpl.mock.calls[1]?.[1] as RequestInit).body).toBe('{}')
    expect((fetchImpl.mock.calls[1]?.[1] as RequestInit).signal).toBe(abort.signal)

    await expect(
      client.createSession(fixture.history.resume.request.resume_session_id),
    ).rejects.toBeInstanceOf(AgentNetworkError)
    expect(fetchImpl).toHaveBeenCalledTimes(3)
    expect((fetchImpl.mock.calls[2]?.[1] as RequestInit).body).toBe(
      JSON.stringify(fixture.history.resume.request),
    )
    expect(Object.keys(JSON.parse(String((fetchImpl.mock.calls[2]?.[1] as RequestInit).body)))).toEqual([
      'resume_session_id',
    ])
  })

  it('parses resume create responses and keeps AbortSignal as the first argument compatible', async () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe('POST')
      return jsonResponse(fixture.history.resume.response, 201)
    })
    const client = new AgentHttpClient({ fetchImpl })
    const created = await client.createSession('session_fixture_1')
    expect(created).toEqual(fixture.history.resume.response)
    expect(created.transport_session_id).toBe('transport_fixture_1')
    expect(created.cursor).toBe(4)

    const abort = new AbortController()
    await client.createSession(undefined, abort.signal)
    expect((fetchImpl.mock.calls[1]?.[1] as RequestInit).body).toBe('{}')
    expect((fetchImpl.mock.calls[1]?.[1] as RequestInit).signal).toBe(abort.signal)
  })

  it('maps history and resume stable errors without exposing backend message text', async () => {
    const cases = fixture.history_errors
    for (const sample of cases) {
      const fetchImpl = vi.fn(async () =>
        jsonResponse(
          { error: { code: sample.code, message: 'private traceback /secret/path.jsonl' } },
          sample.status,
        ),
      )
      const client = new AgentHttpClient({ fetchImpl })
      const method =
        sample.code === 'invalid_resume' || sample.code === 'session_exists'
          ? client.createSession('session_fixture_1')
          : sample.code === 'history_not_found' || sample.code === 'history_unavailable'
            ? client.readSessionHistoryEvents('session_fixture_1')
            : client.listSessionHistory()
      await expect(method).rejects.toMatchObject({
        status: sample.status,
        code: sample.code,
        message: sample.message,
      })
      await expect(method).rejects.toSatisfy((error: unknown) => {
        return error instanceof AgentApiError && !error.message.includes('private') && !error.message.includes('/secret')
      })
    }

    const unknown = new AgentHttpClient({
      fetchImpl: vi.fn(async () =>
        jsonResponse({ error: { code: 'mystery_code', message: 'DROP TABLE sessions' } }, 500),
      ),
    })
    await expect(unknown.listSessionHistory()).rejects.toMatchObject({
      code: 'invalid_history_limit',
      message: 'Agent 服务请求失败',
    })
    await expect(unknown.listSessionHistory()).rejects.toSatisfy((error: unknown) => {
      return error instanceof AgentApiError && !error.message.includes('DROP TABLE')
    })
  })

  it('degrades truncated, unknown, and bad diagnostics from live history responses', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/events')) {
        return jsonResponse({
          session_id: 'session_fixture_1',
          events: [
            fixture.history.events.events[0],
            { schema_version: 99, sequence: 2, payload: {} },
            {
              ...fixture.history.events.events[1],
              sequence: 3,
              payload: fixture.history.truncated_payload,
            },
          ],
          has_more: false,
          next_cursor: null,
          diagnostics: [{ code: 'incomplete_tail', message: 'tail' }, 'bad'],
        })
      }
      return jsonResponse({
        sessions: [
          fixture.history.list.sessions[0],
          { session_id: '../outside' },
          { first_user_message: { text: 'orphan' } },
        ],
        next_cursor: null,
        extra: true,
      })
    })
    const client = new AgentHttpClient({ fetchImpl })
    const listing = await client.listSessionHistory()
    expect(listing.sessions).toHaveLength(1)
    expect(listing.sessions[0]?.session_id).toBe('session_fixture_1')

    const events = await client.readSessionHistoryEvents('session_fixture_1')
    expect(events.events.map((event) => event.sequence)).toEqual([1, 3])
    expect(events.events[1]?.payload).toMatchObject({ truncated: true, head: 'head-preview' })
    expect(events.has_more).toBe(false)
    expect(events.next_cursor).toBeNull()
  })
})
