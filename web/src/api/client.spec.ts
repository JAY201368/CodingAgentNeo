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
