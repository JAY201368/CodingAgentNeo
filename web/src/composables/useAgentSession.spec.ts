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
})
