import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import { AgentHttpClient } from './api/client'
import App from './App.vue'

function event(sequence: number, type: string, payload: Record<string, unknown>): Record<string, unknown> {
  return {
    schema_version: 1,
    session_id: 'session_app_test',
    event_id: `event_app_test_${sequence}`,
    agent_id: 'agent_app_test',
    parent_agent_id: null,
    sequence,
    type,
    correlation_id: null,
    provider_tool_call_id: null,
    timestamp: `2026-09-01T00:00:0${sequence}.000000Z`,
    payload,
  }
}

function nextTask(): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, 0))
}

function makeScriptedClient(options: {
  readonly commandResponse?: Response
  readonly events?: readonly Record<string, unknown>[]
} = {}): { readonly client: AgentHttpClient; readonly commandCalls: ReturnType<typeof vi.fn> } {
  let streamController: ReadableStreamDefaultController<Uint8Array> | null = null
  let eventsPending = false
  const commandCalls = vi.fn()
  const pushEvents = (): void => {
    const encoder = new TextEncoder()
    const frames = (options.events ?? [])
      .map((item) => `id: ${String(item.sequence)}\nevent: agent-event\ndata: ${JSON.stringify(item)}\n\n`)
      .join('')
    if (frames.length === 0) {
      return
    }
    if (streamController === null) {
      eventsPending = true
      return
    }
    streamController.enqueue(encoder.encode(frames))
    streamController.close()
    eventsPending = false
  }
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      streamController = controller
      if (eventsPending) {
        pushEvents()
      }
    },
  })
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path.endsWith('/sessions') && init?.method === 'POST') {
      return new Response(JSON.stringify({
        transport_session_id: 'transport_app_test',
        state: 'RUNNING',
        cursor: 0,
      }), { status: 201 })
    }
    if (path.includes('/events?') || path.endsWith('/events')) {
      return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    }
    if (path.endsWith('/commands')) {
      commandCalls(init?.body)
      if (options.events !== undefined) {
        pushEvents()
      }
      return options.commandResponse ?? new Response(JSON.stringify({ accepted: true }), { status: 202 })
    }
    return new Response(null, { status: 204 })
  })
  return { client: new AgentHttpClient({ fetchImpl }), commandCalls }
}

describe('App', () => {
  it('renders an honest disconnected placeholder', () => {
    const wrapper = mount(App)

    expect(wrapper.get('h1').text()).toBe('CodingAgentNeo Web')
    expect(wrapper.text()).toContain('尚未连接 Agent 服务')
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('creates a session, submits one task, and renders the ordered final timeline', async () => {
    const scripted = makeScriptedClient({
      events: [
        event(1, 'user_message', { text: 'inspect' }),
        event(2, 'assistant_message', { text: 'draft' }),
        event(3, 'turn_end', { state: 'COMPLETED_TURN', assistant_text: 'canonical answer' }),
      ],
    })
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await nextTask()
    await flushPromises()

    const input = wrapper.get('textarea')
    await input.setValue('inspect')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(scripted.commandCalls).toHaveBeenCalledTimes(1)
    expect(scripted.commandCalls.mock.calls[0][0]).toBe('{"type":"SubmitTask","text":"inspect"}')
    expect(wrapper.text()).toContain('用户任务')
    expect(wrapper.text()).toContain('Assistant 回复')
    expect(wrapper.text()).toContain('canonical answer')
    expect(wrapper.text()).toContain('本轮已完成')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)

    wrapper.unmount()
  })

  it('locks duplicate submission while the first POST is pending', async () => {
    let resolveCommand: (response: Response) => void = () => undefined
    const pendingResponse = new Promise<Response>((resolve) => {
      resolveCommand = resolve
    })
    const scripted = makeScriptedClient()
    const pendingFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path.endsWith('/sessions') && init?.method === 'POST') {
        return new Response(JSON.stringify({
          transport_session_id: 'transport_app_test',
          state: 'RUNNING',
          cursor: 0,
        }), { status: 201 })
      }
      if (path.includes('/events?') || path.endsWith('/events')) {
        return new Response('', { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (path.endsWith('/commands')) {
        scripted.commandCalls(init?.body)
        return pendingResponse
      }
      return new Response(null, { status: 204 })
    })
    const client = new AgentHttpClient({ fetchImpl: pendingFetch })

    const wrapper = mount(App, { props: { client, storage: null } })
    await nextTask()
    await flushPromises()
    await wrapper.get('textarea').setValue('inspect')
    const form = wrapper.get('form')
    void form.trigger('submit')
    void form.trigger('submit')
    expect(scripted.commandCalls).toHaveBeenCalledTimes(1)
    resolveCommand(new Response(JSON.stringify({ accepted: true }), { status: 202 }))
    await flushPromises()
    wrapper.unmount()
  })

  it('shows a recoverable safe error for a closed session', async () => {
    const scripted = makeScriptedClient({
      commandResponse: new Response(JSON.stringify({
        error: { code: 'session_closed', message: 'private backend detail' },
      }), { status: 410 }),
    })
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await nextTask()
    await flushPromises()
    await wrapper.get('textarea').setValue('inspect')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('session 已关闭或不存在')
    expect(wrapper.get('[role="alert"]').text()).not.toContain('private backend detail')
    expect(wrapper.text()).toContain('重新连接')
    wrapper.unmount()
  })

  it('renders one aggregated tool card and sends one approval response', async () => {
    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null
    const queuedFrames: string[] = []
    const commandCalls: string[] = []
    const encoder = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller
        for (const frame of queuedFrames.splice(0)) {
          controller.enqueue(encoder.encode(frame))
        }
      },
      cancel() {
        streamController = null
      },
    })
    const push = (items: readonly Record<string, unknown>[]): void => {
      const frames = items
        .map((item) => `id: ${String(item.sequence)}\nevent: agent-event\ndata: ${JSON.stringify(item)}\n\n`)
        .join('')
      if (streamController === null) {
        queuedFrames.push(frames)
      } else {
        streamController.enqueue(encoder.encode(frames))
      }
    }
    const client = new AgentHttpClient({
      fetchImpl: vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        if (path.endsWith('/sessions') && init?.method === 'POST') {
          return new Response(JSON.stringify({
            transport_session_id: 'transport_app_approval',
            state: 'RUNNING',
            cursor: 0,
          }), { status: 201 })
        }
        if (path.includes('/events?')) {
          return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
        }
        if (path.endsWith('/commands')) {
          commandCalls.push(String(init?.body))
          return new Response(JSON.stringify({ accepted: true }), { status: 202 })
        }
        return new Response(null, { status: 204 })
      }),
    })
    const wrapper = mount(App, { props: { client, storage: null } })
    await nextTask()
    await flushPromises()
    await wrapper.get('textarea').setValue('confirm')
    await wrapper.get('form').trigger('submit')
    push([
      event(1, 'user_message', { text: 'confirm' }),
      event(2, 'tool_call', { tool_name: 'read_file', arguments: { secret: 'not rendered' } }),
      {
        ...event(3, 'approval_request', {
          request_id: 'correlation_app_approval',
          tool_name: 'read_file',
          arguments_summary: 'safe redacted summary',
          timeout_seconds: 10,
        }),
        correlation_id: 'correlation_app_approval',
      },
    ].map((item, index) => ({
      ...item,
      correlation_id: index === 0 ? null : 'correlation_app_approval',
    })))
    await flushPromises()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('safe redacted summary')
    expect(wrapper.text()).not.toContain('not rendered')
    await wrapper.get('[role="dialog"] .primary-action').trigger('click')
    await flushPromises()
    await wrapper.get('[role="dialog"] .primary-action').trigger('click')
    expect(commandCalls).toEqual([
      '{"type":"SubmitTask","text":"confirm"}',
      '{"type":"ApprovalResponse","request_id":"correlation_app_approval","approved":true}',
    ])

    push([
      {
        ...event(4, 'policy_decision', { decision: 'allow' }),
        correlation_id: 'correlation_app_approval',
      },
      {
        ...event(5, 'tool_result', {
          result: {
            status: 'success',
            text: 'safe result',
            duration_seconds: 0.25,
            exit_code: 0,
          },
        }),
        correlation_id: 'correlation_app_approval',
      },
    ])
    await flushPromises()
    expect(wrapper.findAll('.tool-card')).toHaveLength(1)
    expect(wrapper.text()).toContain('成功（success）')
    expect(wrapper.text()).toContain('0.250 秒')
    expect(wrapper.text()).toContain('退出码')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('sends Stop once and locks the composer after INTERRUPTED', async () => {
    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null
    const commandCalls: string[] = []
    const encoder = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller
      },
      cancel() {
        streamController = null
      },
    })
    const push = (item: Record<string, unknown>): void => {
      streamController?.enqueue(encoder.encode(
        `id: ${String(item.sequence)}\nevent: agent-event\ndata: ${JSON.stringify(item)}\n\n`,
      ))
    }
    const client = new AgentHttpClient({
      fetchImpl: vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        if (path.endsWith('/sessions') && init?.method === 'POST') {
          return new Response(JSON.stringify({
            transport_session_id: 'transport_app_interrupt',
            state: 'RUNNING',
            cursor: 0,
          }), { status: 201 })
        }
        if (path.includes('/events?')) {
          return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
        }
        if (path.endsWith('/commands')) {
          commandCalls.push(String(init?.body))
          return new Response(JSON.stringify({ accepted: true }), { status: 202 })
        }
        return new Response(null, { status: 204 })
      }),
    })
    const wrapper = mount(App, { props: { client, storage: null } })
    await nextTask()
    await flushPromises()
    await wrapper.get('textarea').setValue('interrupt me')
    await wrapper.get('form').trigger('submit')
    push(event(1, 'user_message', { text: 'interrupt me' }))
    await flushPromises()

    const stop = wrapper.get('.stop-action')
    await stop.trigger('click')
    await flushPromises()
    await stop.trigger('click')
    expect(commandCalls).toEqual([
      '{"type":"SubmitTask","text":"interrupt me"}',
      '{"type":"Interrupt","reason":"user_cancelled"}',
    ])
    expect((stop.element as HTMLButtonElement).disabled).toBe(true)

    push(event(2, 'turn_end', { state: 'INTERRUPTED', reason: 'user_cancelled', assistant_text: '' }))
    await flushPromises()
    expect(wrapper.text()).toContain('已中断')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(true)
    wrapper.unmount()
  })
})
