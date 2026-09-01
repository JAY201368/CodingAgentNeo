import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AgentHttpClient } from './api/client'
import fixture from './domain/fixtures/transport-v1.json'
import App from './App.vue'

const HISTORY_SESSION_ID = fixture.history.resume.request.resume_session_id
const EMPTY_HISTORY_LIST = { sessions: [], next_cursor: null }

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function isHistoryListPath(path: string): boolean {
  return path.includes('/session-history') && !path.includes('/events')
}

function isHistoryEventsPath(path: string): boolean {
  return path.includes('/session-history/') && path.includes('/events')
}

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
    if (isHistoryListPath(path)) {
      return jsonResponse(EMPTY_HISTORY_LIST)
    }
    if (path.endsWith('/sessions') && init?.method === 'POST') {
      return new Response(JSON.stringify({
        transport_session_id: 'transport_app_test',
        state: 'RUNNING',
        cursor: 0,
      }), { status: 201 })
    }
    if (isHistoryEventsPath(path)) {
      return jsonResponse({
        session_id: HISTORY_SESSION_ID,
        events: [],
        next_cursor: null,
        has_more: false,
        diagnostics: [],
      })
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

    expect(wrapper.get('#app-title').text()).toBe('CodingAgentNeo Web')
    expect(wrapper.get('.history-sidebar').element.contains(wrapper.get('#app-title').element)).toBe(true)
    expect(wrapper.get('.app-shell').text()).toContain('尚未连接 Agent 服务')
    expect(wrapper.find('.app-shell button').exists()).toBe(false)
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
    expect(wrapper.text()).toContain('canonical answer')
    expect(wrapper.text()).not.toContain('事件时间线')
    expect(wrapper.text()).toContain('inspect')
    expect(wrapper.text()).not.toContain('Assistant 回复')
    expect(wrapper.get('.timeline__process-toggle').text()).toContain('展开思考过程')
    expect(wrapper.findAll('.timeline__sequence')).toHaveLength(0)

    await wrapper.get('.timeline__process-toggle').trigger('click')
    expect(wrapper.text()).toContain('用户任务')
    expect(wrapper.text()).toContain('Assistant 回复')
    expect(wrapper.findAll('.timeline__sequence').map((item) => item.text())).toEqual([
      '#1', '#2', '#3',
    ])
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)
    const conversation = wrapper.get('.conversation-workspace')
    const children = conversation.element.children
    expect(children[0]?.classList.contains('timeline')).toBe(true)
    expect(children[1]?.classList.contains('composer')).toBe(true)
    expect(wrapper.find('.final-reply').exists()).toBe(false)
    expect(wrapper.find('.session-controls').exists()).toBe(false)
    expect(wrapper.get('.app-header__end-session').text()).toBe('结束 Session')
    expect(wrapper.find('.composer .section-heading').exists()).toBe(false)
    expect(wrapper.find('.composer__reason').exists()).toBe(false)
    expect(wrapper.find('.connection-status').exists()).toBe(false)
    expect(wrapper.find('.runtime-status').exists()).toBe(false)

    await wrapper.get('.app-header__end-session').trigger('click')
    await flushPromises()
    const sessionEntry = wrapper.get('.connection-card--session-entry')
    expect(sessionEntry.get('.connection-card__message').text()).toBe('当前 Session 已结束')
    expect(sessionEntry.get('button').text()).toBe('新建 session')
    const messageTail = wrapper.get('.message-tail')
    expect(messageTail.element.contains(sessionEntry.element)).toBe(true)
    const shellChildren = [...wrapper.get('.app-shell').element.children]
    expect(shellChildren.indexOf(messageTail.element)).toBeGreaterThan(
      shellChildren.indexOf(conversation.element),
    )

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
      if (isHistoryListPath(path)) {
        return jsonResponse(EMPTY_HISTORY_LIST)
      }
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
    expect(wrapper.get('.message-tail').element.contains(wrapper.get('[role="alert"]').element)).toBe(true)
    expect(wrapper.text()).toContain('新建 session')
    wrapper.unmount()
  })

  it('keeps tool events in the folded thought process and sends one approval response', async () => {
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
        if (isHistoryListPath(path)) {
          return jsonResponse(EMPTY_HISTORY_LIST)
        }
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
    expect(wrapper.get('.message-tail').element.contains(wrapper.get('[role="dialog"]').element)).toBe(true)
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
    expect(wrapper.find('.tool-lifecycles').exists()).toBe(false)
    expect(wrapper.find('.tool-card').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('safe result')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    await wrapper.get('.timeline__process-toggle').trigger('click')
    expect(wrapper.text()).toContain('工具结果：success · safe result')
    wrapper.unmount()
  })

  it('keeps the arrow send button disabled while a turn is running', async () => {
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
        if (isHistoryListPath(path)) {
          return jsonResponse(EMPTY_HISTORY_LIST)
        }
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

    expect(wrapper.find('.run-controls').exists()).toBe(false)
    expect(wrapper.find('.cancel-action').exists()).toBe(false)
    const send = wrapper.get('.composer__send')
    expect(send.attributes('aria-label')).toBe('发送任务')
    expect((send.element as HTMLButtonElement).disabled).toBe(true)
    expect(commandCalls).toEqual(['{"type":"SubmitTask","text":"interrupt me"}'])

    push(event(2, 'turn_end', { state: 'INTERRUPTED', reason: 'user_cancelled', assistant_text: '' }))
    await flushPromises()
    expect(wrapper.text()).toContain('INTERRUPTED')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(true)
    wrapper.unmount()
  })
})

type RecordedCall = {
  readonly method: string
  readonly path: string
  readonly body: unknown
}

function requestBody(init?: RequestInit): unknown {
  if (typeof init?.body !== 'string' || init.body.length === 0) {
    return null
  }
  try {
    return JSON.parse(init.body) as unknown
  } catch {
    return init.body
  }
}

function historyHydrationPage(): Record<string, unknown> {
  const userText = fixture.history.list.sessions[0]?.first_user_message.text ?? '请检查失败测试'
  return {
    session_id: HISTORY_SESSION_ID,
    events: [
      {
        ...fixture.events[0],
        sequence: 1,
        type: 'session_start',
        payload: { state: 'RUNNING' },
      },
      {
        ...fixture.events[0],
        event_id: 'event_history_user',
        sequence: 2,
        type: 'user_message',
        payload: { text: userText },
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
    ],
    next_cursor: null,
    has_more: false,
    diagnostics: [],
  }
}

function makeHistoryAppClient(options: {
  readonly resumeResponse?: Response
  readonly holdDelete?: Promise<void>
} = {}): {
  readonly client: AgentHttpClient
  readonly calls: RecordedCall[]
  readonly commandCalls: string[]
} {
  const calls: RecordedCall[] = []
  const commandCalls: string[] = []
  const stream = new ReadableStream<Uint8Array>({
    start() {
      return undefined
    },
  })
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const method = init?.method ?? 'GET'
    const body = requestBody(init)
    calls.push({ method, path, body })
    if (isHistoryListPath(path)) {
      return jsonResponse(fixture.history.list)
    }
    if (path.endsWith('/sessions') && method === 'POST') {
      const record = body !== null && typeof body === 'object' ? body as Record<string, unknown> : {}
      if (typeof record.resume_session_id === 'string') {
        if (options.resumeResponse !== undefined) {
          return options.resumeResponse
        }
        return jsonResponse(fixture.history.resume.response, 201)
      }
      return jsonResponse({
        transport_session_id: 'transport_app_live',
        state: 'RUNNING',
        cursor: 0,
      }, 201)
    }
    if (method === 'DELETE') {
      if (options.holdDelete !== undefined) {
        await options.holdDelete
      }
      return new Response(null, { status: 204 })
    }
    if (isHistoryEventsPath(path)) {
      return jsonResponse(historyHydrationPage())
    }
    if (path.includes('/events')) {
      return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    }
    if (path.endsWith('/commands')) {
      commandCalls.push(String(init?.body))
      return jsonResponse({ accepted: true }, 202)
    }
    return new Response(null, { status: 204 })
  })
  return { client: new AgentHttpClient({ fetchImpl }), calls, commandCalls }
}

function sessionPosts(calls: readonly RecordedCall[]): RecordedCall[] {
  return calls.filter((call) => call.method === 'POST' && call.path.endsWith('/sessions'))
}

describe('App history sidebar wiring', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('places the title in the sidebar and keeps the conversation in a centered main column', async () => {
    const scripted = makeHistoryAppClient()
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await nextTask()
    await flushPromises()

    const sidebar = wrapper.get('.history-sidebar')
    expect(sidebar.get('#app-title').text()).toBe('CodingAgentNeo Web')
    expect(sidebar.text()).toContain('请检查失败测试')
    expect(wrapper.get('.app-layout')).toBeTruthy()
    expect(wrapper.get('.app-main .app-shell')).toBeTruthy()
    expect(wrapper.get('.app-main .conversation-workspace')).toBeTruthy()
    expect(wrapper.get('.app-main .composer')).toBeTruthy()
    expect(wrapper.find('.app-shell #app-title').exists()).toBe(false)
    wrapper.unmount()
  })

  it('resumes the selected history session, hydrates turns, and allows follow-up', async () => {
    const scripted = makeHistoryAppClient()
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await nextTask()
    await flushPromises()

    await wrapper.get('.history-sidebar__select').trigger('click')
    await flushPromises()
    await nextTask()
    await flushPromises()

    const deleteIndex = scripted.calls.findIndex((call) => call.method === 'DELETE')
    const resumeIndex = scripted.calls.findIndex((call) => {
      const body = call.body
      return call.method === 'POST' &&
        call.path.endsWith('/sessions') &&
        typeof body === 'object' &&
        body !== null &&
        'resume_session_id' in body
    })
    const historyEventsIndex = scripted.calls.findIndex((call) =>
      call.method === 'GET' && isHistoryEventsPath(call.path),
    )
    const liveEventsIndex = scripted.calls.findIndex((call, index) =>
      index > historyEventsIndex &&
      call.method === 'GET' &&
      call.path.includes('/events') &&
      !isHistoryEventsPath(call.path),
    )
    expect(deleteIndex).toBeGreaterThanOrEqual(0)
    expect(resumeIndex).toBeGreaterThan(deleteIndex)
    expect(historyEventsIndex).toBeGreaterThan(resumeIndex)
    expect(liveEventsIndex).toBeGreaterThan(historyEventsIndex)
    expect(scripted.calls[deleteIndex]?.path).toContain('transport_app_live')
    expect(scripted.calls[deleteIndex]?.path).not.toContain(HISTORY_SESSION_ID)
    expect(scripted.calls[resumeIndex]?.body).toEqual({ resume_session_id: HISTORY_SESSION_ID })
    expect(scripted.calls[historyEventsIndex]?.path).toContain(`/session-history/${HISTORY_SESSION_ID}/events`)
    expect(wrapper.get('.history-sidebar__select').attributes('aria-current')).toBe('true')
    expect(wrapper.get('.app-main').text()).toContain('请检查失败测试')
    expect(wrapper.get('.app-main').text()).toContain('done')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)

    await wrapper.get('textarea').setValue('follow-up')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(scripted.commandCalls).toEqual(['{"type":"SubmitTask","text":"follow-up"}'])
    wrapper.unmount()
  })

  it('disables the composer and sidebar while a switch is in flight', async () => {
    let releaseDelete: (() => void) | undefined
    const holdDelete = new Promise<void>((resolve) => {
      releaseDelete = resolve
    })
    const scripted = makeHistoryAppClient({ holdDelete })
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await nextTask()
    await flushPromises()

    await wrapper.get('.history-sidebar__select').trigger('click')
    await flushPromises()
    await nextTask()

    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(true)
    expect((wrapper.get('.history-sidebar__select').element as HTMLButtonElement).disabled).toBe(true)
    expect(scripted.calls.some((call) => {
      const body = call.body
      return call.method === 'POST' &&
        typeof body === 'object' &&
        body !== null &&
        'resume_session_id' in body
    })).toBe(false)

    releaseDelete?.()
    await flushPromises()
    await nextTask()
    await flushPromises()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)
    wrapper.unmount()
  })

  it.each([
    { status: 404, code: 'history_not_found', message: 'private backend detail' },
    { status: 422, code: 'history_unavailable', message: 'DROP TABLE sessions' },
    { status: 422, code: 'invalid_resume', message: 'cannot resume this file' },
    { status: 409, code: 'session_exists', message: 'an active transport session already exists' },
  ])('fail-closes after resume $status $code without auto-creating a session', async ({ status, code, message }) => {
    const scripted = makeHistoryAppClient({
      resumeResponse: jsonResponse({ error: { code, message } }, status),
    })
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await nextTask()
    await flushPromises()

    await wrapper.get('.history-sidebar__select').trigger('click')
    await flushPromises()
    await nextTask()
    await flushPromises()

    const posts = sessionPosts(scripted.calls)
    expect(posts).toHaveLength(2)
    expect(posts[0]?.body).toEqual({})
    expect(posts[1]?.body).toEqual({ resume_session_id: HISTORY_SESSION_ID })
    expect(wrapper.find('.conversation-workspace').exists()).toBe(false)
    expect(wrapper.get('.app-shell [role="alert"]').text()).not.toContain(message)
    expect(wrapper.get('.app-shell [role="alert"]').text()).not.toContain('请关闭其他页面')
    expect(wrapper.get('.connection-card--session-entry button').text()).toBe('新建 session')
    expect(wrapper.get('.history-sidebar__select').attributes('aria-current')).toBeUndefined()
    wrapper.unmount()
  })

  it('does not switch when the active-turn confirmation is cancelled', async () => {
    const scripted = makeHistoryAppClient()
    const confirm = vi.spyOn(globalThis, 'confirm').mockReturnValue(false)
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await nextTask()
    await flushPromises()

    await wrapper.get('textarea').setValue('keep working')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    await wrapper.get('.history-sidebar__select').trigger('click')
    await flushPromises()

    expect(confirm).toHaveBeenCalledWith('将终结当前正在进行的工作并切换 session')
    expect(scripted.calls.some((call) => call.method === 'DELETE')).toBe(false)
    expect(sessionPosts(scripted.calls)).toHaveLength(1)
    expect(wrapper.find('.conversation-workspace').exists()).toBe(true)
    wrapper.unmount()
  })

  it('switches after the active-turn confirmation is accepted', async () => {
    const scripted = makeHistoryAppClient()
    const confirm = vi.spyOn(globalThis, 'confirm').mockReturnValue(true)
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await nextTask()
    await flushPromises()

    await wrapper.get('textarea').setValue('leave this turn')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    await wrapper.get('.history-sidebar__select').trigger('click')
    await flushPromises()
    await nextTask()
    await flushPromises()

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(scripted.calls.some((call) => call.method === 'DELETE')).toBe(true)
    expect(sessionPosts(scripted.calls).some((call) => {
      const body = call.body
      return typeof body === 'object' && body !== null && 'resume_session_id' in body
    })).toBe(true)
    expect(wrapper.get('.app-main').text()).toContain('done')
    wrapper.unmount()
  })
})
