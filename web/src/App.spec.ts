import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AgentHttpClient } from './api/client'
import './style.css'
import { DEFAULT_STORAGE_KEY } from './composables/useAgentSession'
import fixture from './domain/fixtures/transport-v1.json'
import App from './App.vue'

const HISTORY_SESSION_ID = fixture.history.resume.request.resume_session_id
const EMPTY_HISTORY_LIST = { sessions: [], next_cursor: null }
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

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function pathnameOf(path: string): string {
  try {
    return new URL(path, 'http://local.invalid').pathname
  } catch {
    return path.split('?')[0] ?? path
  }
}

function isHistoryListPath(path: string): boolean {
  return path.includes('/session-history') && !path.includes('/events')
}

function isHistoryEventsPath(path: string): boolean {
  return path.includes('/session-history/') && path.includes('/events')
}

function isSessionCollectionPath(path: string): boolean {
  return pathnameOf(path) === '/api/v1/sessions'
}

function isSessionStatusPath(path: string): boolean {
  return /^\/api\/v1\/sessions\/[^/]+$/.test(pathnameOf(path))
}

function isLiveSsePath(path: string): boolean {
  return /^\/api\/v1\/sessions\/[^/]+\/events$/.test(pathnameOf(path))
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

async function settle(): Promise<void> {
  await nextTask()
  await flushPromises()
  await nextTask()
  await flushPromises()
}

function createButton(wrapper: VueWrapper): ReturnType<VueWrapper['get']> {
  return wrapper.get('.history-sidebar [aria-label="新建 session"]')
}

function expectNoMainLifecycleControls(wrapper: VueWrapper): void {
  const labels = wrapper.findAll('.app-shell button').map((button) => {
    const aria = button.attributes('aria-label') ?? ''
    return `${button.text()} ${aria}`.trim()
  })
  expect(labels.some((label) => label.includes('结束 Session'))).toBe(false)
  expect(labels.some((label) => label.includes('重新连接事件流'))).toBe(false)
  expect(labels.some((label) => label.includes('重新连接'))).toBe(false)
  expect(labels.some((label) => label.includes('新建 session'))).toBe(false)
  expect(wrapper.find('.app-shell .app-header__end-session').exists()).toBe(false)
  expect(wrapper.find('.app-shell .connection-card').exists()).toBe(false)
  expect(wrapper.find('.app-shell .connection-card--session-entry').exists()).toBe(false)
}

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>()

  constructor(initial?: { readonly transportSessionId: string; readonly cursor: number }) {
    if (initial !== undefined) {
      this.values.set(DEFAULT_STORAGE_KEY, JSON.stringify(initial))
    }
  }

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

async function createEmptySession(wrapper: VueWrapper): Promise<void> {
  await createButton(wrapper).trigger('click')
  await settle()
}

describe('App conversation after explicit create', () => {
  it('switches the session permission mode from the composer tabs', async () => {
    const scripted = makeScriptedClient()
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()
    await createEmptySession(wrapper)

    const trigger = wrapper.get('.composer__permission-trigger')
    expect(trigger.text()).toContain('询问')
    await trigger.trigger('click')
    const options = wrapper.findAll('.composer__permission-option')
    expect(options).toHaveLength(3)
    await options[1].trigger('click')
    await flushPromises()

    expect(scripted.commandCalls).toHaveBeenCalledWith(
      '{"type":"SetApprovalMode","mode":"auto"}',
    )
    expect(wrapper.get('.composer__permission-trigger').text()).toContain('自动')
    expect(wrapper.find('.composer__permission-menu').exists()).toBe(false)
    wrapper.unmount()
  })

  it('creates a session from the sidebar, submits one task, and renders the ordered final timeline', async () => {
    const scripted = makeScriptedClient({
      events: [
        event(1, 'user_message', { text: 'inspect' }),
        event(2, 'assistant_message', { text: 'draft' }),
        event(3, 'turn_end', { state: 'COMPLETED_TURN', assistant_text: 'canonical answer' }),
      ],
    })
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()
    await createEmptySession(wrapper)

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
    expect(wrapper.findAll('.timeline__type')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('turn_end')

    await wrapper.get('.timeline__process-toggle').trigger('click')
    expect(wrapper.text()).toContain('用户任务')
    expect(wrapper.text()).toContain('Assistant 回复')
    expect(wrapper.findAll('.timeline__sequence')).toHaveLength(0)
    expect(wrapper.findAll('.timeline__type')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('#1')
    expect(wrapper.text()).not.toContain('turn_end')
    expect(wrapper.text()).not.toContain('user_message')
    expect(wrapper.text()).not.toContain('assistant_message')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)
    const conversation = wrapper.get('.conversation-workspace')
    const children = conversation.element.children
    expect(children[0]?.classList.contains('conversation-workspace__scroll')).toBe(true)
    expect(children[1]?.classList.contains('composer')).toBe(true)
    expect(conversation.find('.conversation-workspace__scroll .timeline').exists()).toBe(true)
    expect(wrapper.find('.final-reply').exists()).toBe(false)
    expect(wrapper.find('.session-controls').exists()).toBe(false)
    expectNoMainLifecycleControls(wrapper)
    expect(wrapper.find('.composer .section-heading').exists()).toBe(false)
    expect(wrapper.find('.composer__reason').exists()).toBe(false)
    expect(wrapper.find('.connection-status').exists()).toBe(false)
    expect(wrapper.find('.runtime-status').exists()).toBe(false)

    wrapper.unmount()
  })

  it('hides session/agent lifecycle events even after expanding the thinking process', async () => {
    const scripted = makeScriptedClient({
      events: [
        event(1, 'session_start', { state: 'RUNNING' }),
        event(2, 'agent_start', { state: 'RUNNING', active_tools: ['read_file'] }),
        event(3, 'user_message', { text: 'inspect' }),
        event(4, 'assistant_message', { text: 'draft' }),
        event(5, 'tool_call', { tool_name: 'read_file' }),
        event(6, 'policy_decision', { decision: 'allow' }),
        event(7, 'tool_result', { result: { status: 'success', text: 'ok' } }),
        event(8, 'turn_end', { state: 'COMPLETED_TURN', assistant_text: 'canonical answer' }),
      ],
    })
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()
    await createEmptySession(wrapper)

    await wrapper.get('textarea').setValue('inspect')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    await wrapper.get('.timeline__process-toggle').trigger('click')
    expect(wrapper.text()).toContain('Assistant 回复')
    expect(wrapper.text()).toContain('工具调用')
    expect(wrapper.text()).toContain('策略决定')
    expect(wrapper.text()).toContain('工具结果')
    expect(wrapper.text()).not.toContain('Session 开始')
    expect(wrapper.text()).not.toContain('Agent 开始')
    expect(wrapper.text()).not.toContain('Agent 结束')
    expect(wrapper.text()).not.toContain('Session 结束')
    expect(wrapper.findAll('.timeline__sequence')).toHaveLength(0)
    expect(wrapper.findAll('.timeline__type')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('turn_end')
    expect(wrapper.text()).not.toContain('tool_call')
    expect(wrapper.text()).not.toContain('assistant_message')
    expect(wrapper.text()).not.toContain('policy_decision')
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
    await settle()
    await createEmptySession(wrapper)
    await wrapper.get('textarea').setValue('inspect')
    const form = wrapper.get('form')
    void form.trigger('submit')
    void form.trigger('submit')
    expect(scripted.commandCalls).toHaveBeenCalledTimes(1)
    resolveCommand(new Response(JSON.stringify({ accepted: true }), { status: 202 }))
    await flushPromises()
    wrapper.unmount()
  })

  it('shows a recoverable safe error for a closed session without a main-area create button', async () => {
    const scripted = makeScriptedClient({
      commandResponse: new Response(JSON.stringify({
        error: { code: 'session_closed', message: 'private backend detail' },
      }), { status: 410 }),
    })
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()
    await createEmptySession(wrapper)
    await wrapper.get('textarea').setValue('inspect')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('session 已关闭或不存在')
    expect(wrapper.get('[role="alert"]').text()).toContain('左侧侧边栏')
    expect(wrapper.get('[role="alert"]').text()).not.toContain('private backend detail')
    expect(wrapper.get('.message-tail').element.contains(wrapper.get('[role="alert"]').element)).toBe(true)
    expectNoMainLifecycleControls(wrapper)
    expect(createButton(wrapper).attributes('aria-label')).toBe('新建 session')
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
    await settle()
    await createEmptySession(wrapper)
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
    await settle()
    await createEmptySession(wrapper)
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

function interruptedHydrationPage(): Record<string, unknown> {
  const page = historyHydrationPage()
  const events = [
    ...(page.events as Record<string, unknown>[]),
    {
      ...fixture.events[3],
      event_id: 'event_history_session_end',
      sequence: 5,
      type: 'session_end',
      payload: { state: 'INTERRUPTED', reason: 'user_cancelled', budget: {} },
    },
  ]
  return {
    ...page,
    events,
  }
}

function makeHistoryAppClient(options: {
  readonly resumeResponse?: Response
  readonly holdDelete?: Promise<void>
  readonly holdCreate?: Promise<void>
  readonly historyEvents?: Record<string, unknown>
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
      const url = new URL(path, 'http://local.invalid')
      if (url.searchParams.get('cursor') === 'opaque_history_cursor_fixture_1') {
        return jsonResponse({
          sessions: [PAGE_TWO_SESSION],
          next_cursor: null,
        })
      }
      return jsonResponse(fixture.history.list)
    }
    if (isSessionCollectionPath(path) && method === 'POST') {
      const record = body !== null && typeof body === 'object' ? body as Record<string, unknown> : {}
      if (typeof record.resume_session_id === 'string') {
        if (options.resumeResponse !== undefined) {
          return options.resumeResponse
        }
        return jsonResponse(fixture.history.resume.response, 201)
      }
      if (options.holdCreate !== undefined) {
        await options.holdCreate
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
      return jsonResponse(options.historyEvents ?? historyHydrationPage())
    }
    if (isLiveSsePath(path) || path.includes('/events')) {
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
  return calls.filter((call) => call.method === 'POST' && isSessionCollectionPath(call.path))
}

function isResumePost(call: RecordedCall): boolean {
  const body = call.body
  return call.method === 'POST' &&
    isSessionCollectionPath(call.path) &&
    typeof body === 'object' &&
    body !== null &&
    'resume_session_id' in body
}

function isCreatePost(call: RecordedCall): boolean {
  return call.method === 'POST' &&
    isSessionCollectionPath(call.path) &&
    (call.body === null || (typeof call.body === 'object' && call.body !== null && !('resume_session_id' in call.body)))
}

function sessionStatusGets(calls: readonly RecordedCall[]): RecordedCall[] {
  return calls.filter((call) => call.method === 'GET' && isSessionStatusPath(call.path))
}

function liveSseGets(calls: readonly RecordedCall[]): RecordedCall[] {
  return calls.filter((call) => call.method === 'GET' && isLiveSsePath(call.path))
}

function deletes(calls: readonly RecordedCall[]): RecordedCall[] {
  return calls.filter((call) => call.method === 'DELETE')
}

describe('App idle and sidebar lifecycle', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('loads history on first mount with zero session side effects and a blank main pane', async () => {
    const scripted = makeHistoryAppClient()
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()

    const listCalls = scripted.calls.filter((call) => call.method === 'GET' && isHistoryListPath(call.path))
    expect(listCalls.length).toBeGreaterThan(0)
    expect(sessionPosts(scripted.calls)).toHaveLength(0)
    expect(sessionStatusGets(scripted.calls)).toHaveLength(0)
    expect(liveSseGets(scripted.calls)).toHaveLength(0)
    expect(deletes(scripted.calls)).toHaveLength(0)
    expect(wrapper.find('.conversation-workspace').exists()).toBe(false)
    expect(wrapper.find('.composer').exists()).toBe(false)
    expect(wrapper.get('.app-shell').text()).not.toContain('尚未连接 Agent 服务')
    expect(wrapper.get('.app-shell').text()).not.toContain('当前 Session 已结束')
    expect(wrapper.get('.app-shell').text()).not.toContain('连接已中断')
    expectNoMainLifecycleControls(wrapper)
    expect(createButton(wrapper).attributes('aria-label')).toBe('新建 session')
    wrapper.unmount()
  })

  it('does not attach a persisted transport hint on mount', async () => {
    const scripted = makeHistoryAppClient()
    const storage = new MemoryStorage({
      transportSessionId: 'transport_persisted_hint',
      cursor: 7,
    })
    const wrapper = mount(App, { props: { client: scripted.client, storage } })
    await settle()

    expect(sessionPosts(scripted.calls)).toHaveLength(0)
    expect(sessionStatusGets(scripted.calls)).toHaveLength(0)
    expect(liveSseGets(scripted.calls)).toHaveLength(0)
    expect(deletes(scripted.calls)).toHaveLength(0)
    expect(scripted.calls.some((call) => call.path.includes('transport_persisted_hint'))).toBe(false)
    expect(wrapper.find('.conversation-workspace').exists()).toBe(false)
    wrapper.unmount()
  })

  it('creates from the circular button, DELETEs a known transport first, and clears the current history item', async () => {
    const scripted = makeHistoryAppClient()
    const storage = new MemoryStorage({
      transportSessionId: 'transport_persisted_hint',
      cursor: 3,
    })
    const wrapper = mount(App, { props: { client: scripted.client, storage } })
    await settle()

    await wrapper.get('.history-sidebar__select').trigger('click')
    await settle()
    expect(wrapper.get('.history-sidebar__select').attributes('aria-current')).toBe('true')

    const beforeCreate = scripted.calls.length
    await createButton(wrapper).trigger('click')
    await settle()

    const createdCalls = scripted.calls.slice(beforeCreate)
    const deleteIndex = createdCalls.findIndex((call) => call.method === 'DELETE')
    const createIndex = createdCalls.findIndex((call) => isCreatePost(call))
    expect(deleteIndex).toBeGreaterThanOrEqual(0)
    expect(createIndex).toBeGreaterThan(deleteIndex)
    expect(createdCalls[deleteIndex]?.path).toContain('transport_fixture_1')
    expect(createdCalls[createIndex]?.body).toEqual({})
    expect(wrapper.find('.conversation-workspace').exists()).toBe(true)
    expect(wrapper.find('.composer').exists()).toBe(true)
    expect(wrapper.get('.history-sidebar__select').attributes('aria-current')).toBeUndefined()
    expectNoMainLifecycleControls(wrapper)
    wrapper.unmount()
  })

  it('resumes a history item, hydrates turns, and allows follow-up', async () => {
    const scripted = makeHistoryAppClient()
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()

    await wrapper.get('.history-sidebar__select').trigger('click')
    await settle()

    const resumeIndex = scripted.calls.findIndex((call) => isResumePost(call))
    const historyEventsIndex = scripted.calls.findIndex((call) =>
      call.method === 'GET' && isHistoryEventsPath(call.path),
    )
    const liveEventsIndex = scripted.calls.findIndex((call, index) =>
      index > historyEventsIndex && call.method === 'GET' && isLiveSsePath(call.path),
    )
    expect(deletes(scripted.calls)).toHaveLength(0)
    expect(resumeIndex).toBeGreaterThanOrEqual(0)
    expect(historyEventsIndex).toBeGreaterThan(resumeIndex)
    expect(liveEventsIndex).toBeGreaterThan(historyEventsIndex)
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

  it('DELETEs a persisted transport before resume replacement', async () => {
    const scripted = makeHistoryAppClient()
    const storage = new MemoryStorage({
      transportSessionId: 'transport_persisted_hint',
      cursor: 4,
    })
    const wrapper = mount(App, { props: { client: scripted.client, storage } })
    await settle()

    await wrapper.get('.history-sidebar__select').trigger('click')
    await settle()

    const deleteIndex = scripted.calls.findIndex((call) => call.method === 'DELETE')
    const resumeIndex = scripted.calls.findIndex((call) => isResumePost(call))
    expect(deleteIndex).toBeGreaterThanOrEqual(0)
    expect(resumeIndex).toBeGreaterThan(deleteIndex)
    expect(scripted.calls[deleteIndex]?.path).toContain('transport_persisted_hint')
    expect(scripted.calls[deleteIndex]?.path).not.toContain(HISTORY_SESSION_ID)
    expect(scripted.calls[resumeIndex]?.body).toEqual({ resume_session_id: HISTORY_SESSION_ID })
    wrapper.unmount()
  })

  it('restores a history session that ended INTERRUPTED without a fake disconnect entry', async () => {
    const scripted = makeHistoryAppClient({
      historyEvents: interruptedHydrationPage(),
    })
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()

    await wrapper.get('.history-sidebar__select').trigger('click')
    await settle()

    expect(wrapper.get('.app-main').text()).toContain('请检查失败测试')
    expect(wrapper.get('.app-main').text()).toContain('done')
    expect(wrapper.find('.conversation-workspace').exists()).toBe(true)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)
    expect(wrapper.get('.app-shell').text()).not.toContain('当前 Session 连接已中断')
    expect(wrapper.get('.app-shell').text()).not.toContain('当前 Session 已结束')
    expectNoMainLifecycleControls(wrapper)

    await wrapper.get('textarea').setValue('continue')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(scripted.commandCalls).toEqual(['{"type":"SubmitTask","text":"continue"}'])
    wrapper.unmount()
  })

  it('disables create, history selection, and composer while a replacement is in flight', async () => {
    let releaseDelete: (() => void) | undefined
    const holdDelete = new Promise<void>((resolve) => {
      releaseDelete = resolve
    })
    const scripted = makeHistoryAppClient({ holdDelete })
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()
    await createEmptySession(wrapper)

    await wrapper.get('.history-sidebar__select').trigger('click')
    await flushPromises()
    await nextTask()

    expect((createButton(wrapper).element as HTMLButtonElement).disabled).toBe(true)
    expect((wrapper.get('.history-sidebar__select').element as HTMLButtonElement).disabled).toBe(true)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(true)
    expect(wrapper.get('[role="status"]').text()).toMatch(/正在切换 session|正在新建 session/)
    expect(scripted.calls.some((call) => isResumePost(call))).toBe(false)

    releaseDelete?.()
    await settle()
    expect((createButton(wrapper).element as HTMLButtonElement).disabled).toBe(false)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)
    wrapper.unmount()
  })

  it('disables create and selection while a create replacement is in flight', async () => {
    let releaseCreate: (() => void) | undefined
    const holdCreate = new Promise<void>((resolve) => {
      releaseCreate = resolve
    })
    const scripted = makeHistoryAppClient({ holdCreate })
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()

    await createButton(wrapper).trigger('click')
    await flushPromises()
    await nextTask()

    expect((createButton(wrapper).element as HTMLButtonElement).disabled).toBe(true)
    expect((wrapper.get('.history-sidebar__select').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.get('[role="status"]').text()).toContain('正在新建 session')
    expect(sessionPosts(scripted.calls).every((call) => isCreatePost(call))).toBe(true)
    expect(sessionPosts(scripted.calls).some((call) => isResumePost(call))).toBe(false)

    releaseCreate?.()
    await settle()
    expect((createButton(wrapper).element as HTMLButtonElement).disabled).toBe(false)
    expect(wrapper.find('.composer').exists()).toBe(true)
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
    await settle()

    await wrapper.get('.history-sidebar__select').trigger('click')
    await settle()

    const posts = sessionPosts(scripted.calls)
    expect(posts).toHaveLength(1)
    expect(posts[0]?.body).toEqual({ resume_session_id: HISTORY_SESSION_ID })
    expect(wrapper.find('.conversation-workspace').exists()).toBe(false)
    expect(wrapper.get('.app-shell [role="alert"]').text()).not.toContain(message)
    expect(wrapper.get('.app-shell [role="alert"]').text()).not.toContain('请关闭其他页面')
    expect(wrapper.get('.app-shell [role="alert"]').text()).toContain('左侧侧边栏')
    expectNoMainLifecycleControls(wrapper)
    expect(wrapper.get('.history-sidebar__select').attributes('aria-current')).toBeUndefined()
    wrapper.unmount()
  })

  it('does not switch when the active-turn confirmation is cancelled', async () => {
    const scripted = makeHistoryAppClient()
    const confirm = vi.spyOn(globalThis, 'confirm').mockReturnValue(false)
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()
    await createEmptySession(wrapper)

    await wrapper.get('textarea').setValue('keep working')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    await wrapper.get('.history-sidebar__select').trigger('click')
    await flushPromises()

    expect(confirm).toHaveBeenCalledWith('将终结当前正在进行的工作并切换 session')
    expect(deletes(scripted.calls)).toHaveLength(0)
    expect(sessionPosts(scripted.calls)).toHaveLength(1)
    expect(sessionPosts(scripted.calls)[0]?.body).toEqual({})
    expect(wrapper.find('.conversation-workspace').exists()).toBe(true)
    wrapper.unmount()
  })

  it('does not create when the active-turn confirmation is cancelled', async () => {
    const scripted = makeHistoryAppClient()
    const confirm = vi.spyOn(globalThis, 'confirm').mockReturnValue(false)
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()
    await createEmptySession(wrapper)

    await wrapper.get('textarea').setValue('keep working')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    await createButton(wrapper).trigger('click')
    await flushPromises()

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(deletes(scripted.calls)).toHaveLength(0)
    expect(sessionPosts(scripted.calls)).toHaveLength(1)
    wrapper.unmount()
  })

  it('switches after the active-turn confirmation is accepted', async () => {
    const scripted = makeHistoryAppClient()
    const confirm = vi.spyOn(globalThis, 'confirm').mockReturnValue(true)
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()
    await createEmptySession(wrapper)

    await wrapper.get('textarea').setValue('leave this turn')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    await wrapper.get('.history-sidebar__select').trigger('click')
    await settle()

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(deletes(scripted.calls).length).toBeGreaterThan(0)
    expect(sessionPosts(scripted.calls).some((call) => isResumePost(call))).toBe(true)
    expect(wrapper.get('.app-main').text()).toContain('done')
    wrapper.unmount()
  })

  it('places the title in the sidebar and keeps the main pane blank until the user acts', async () => {
    const scripted = makeHistoryAppClient()
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()

    const sidebar = wrapper.get('.history-sidebar')
    expect(sidebar.get('#app-title').text()).toBe('CodingAgentNeo')
    expect(sidebar.text()).not.toContain('CodingAgentNeo Web')
    expect(sidebar.text()).toContain('请检查失败测试')
    expect(wrapper.get('.app-layout')).toBeTruthy()
    expect(wrapper.get('.app-main .app-shell')).toBeTruthy()
    expect(wrapper.find('.app-main .conversation-workspace').exists()).toBe(false)
    expect(wrapper.find('.app-shell #app-title').exists()).toBe(false)
    expectNoMainLifecycleControls(wrapper)
    wrapper.unmount()
  })

  it('lists history and pages with the opaque next_cursor', async () => {
    const scripted = makeHistoryAppClient()
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()

    const sidebar = wrapper.get('.history-sidebar')
    expect(sidebar.text()).toContain('请检查失败测试')
    expect(wrapper.find('.history-sidebar__load-more').exists()).toBe(true)

    await wrapper.get('.history-sidebar__load-more').trigger('click')
    await flushPromises()

    const listCalls = scripted.calls.filter((call) =>
      call.method === 'GET' && isHistoryListPath(call.path),
    )
    expect(listCalls[0]?.path).toBe('/api/v1/session-history')
    expect(listCalls.some((call) =>
      call.path.includes('cursor=opaque_history_cursor_fixture_1'),
    )).toBe(true)
    expect(sidebar.text()).toContain('page two')
    expect(wrapper.find('.history-sidebar__load-more').exists()).toBe(false)
    wrapper.unmount()
  })
})

type MediaListener = (event: MediaQueryListEvent) => void

function stubMatchMedia(matches: boolean): void {
  const listeners = new Set<MediaListener>()
  vi.stubGlobal('matchMedia', (query: string) => {
    const media = {
      get matches() {
        return query.includes('max-width: 640px') ? matches : false
      },
      media: query,
      addEventListener(_type: string, listener: EventListenerOrEventListenerObject) {
        if (typeof listener === 'function') {
          listeners.add(listener as MediaListener)
        }
      },
      removeEventListener(_type: string, listener: EventListenerOrEventListenerObject) {
        if (typeof listener === 'function') {
          listeners.delete(listener as MediaListener)
        }
      },
      addListener(listener: MediaListener) {
        listeners.add(listener)
      },
      removeListener(listener: MediaListener) {
        listeners.delete(listener)
      },
      dispatchEvent() {
        return false
      },
      onchange: null,
    }
    return media
  })
}

describe('App history drawer layout', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('does not render the history toggle on a desktop viewport', async () => {
    stubMatchMedia(false)
    const scripted = makeHistoryAppClient()
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()

    expect(wrapper.find('.history-drawer-toggle').exists()).toBe(false)
    expect(wrapper.find('.history-drawer-backdrop').exists()).toBe(false)
    expect(wrapper.get('.app-layout').classes()).not.toContain('app-layout--drawer-open')
    expect(wrapper.get('#history-sidebar').attributes('inert')).toBeUndefined()
    expect(wrapper.get('#history-sidebar').attributes('aria-hidden')).toBeUndefined()
    expect(wrapper.get('.app-shell').attributes('inert')).toBeUndefined()
    wrapper.unmount()
  })

  it('opens the history drawer from the toggle and closes it with the backdrop and Escape', async () => {
    stubMatchMedia(true)
    const scripted = makeHistoryAppClient()
    const wrapper = mount(App, {
      props: { client: scripted.client, storage: null },
      attachTo: document.body,
    })
    await settle()

    const toggle = wrapper.get('.history-drawer-toggle')
    expect(toggle.text()).toContain('历史')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(toggle.attributes('aria-controls')).toBe('history-sidebar')
    expect(wrapper.get('#history-sidebar').attributes('inert')).not.toBeUndefined()
    expect(wrapper.get('#history-sidebar').attributes('aria-hidden')).toBe('true')
    expect(wrapper.find('.history-drawer-backdrop').exists()).toBe(false)
    expect(wrapper.get('.app-shell').attributes('inert')).toBeUndefined()

    await toggle.trigger('click')
    await flushPromises()
    expect(wrapper.get('.history-drawer-toggle').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('.history-drawer-toggle').attributes('aria-label')).toBe('关闭历史')
    expect(wrapper.get('.app-layout').classes()).toContain('app-layout--drawer-open')
    expect(wrapper.get('#history-sidebar').classes()).toContain('history-sidebar--open')
    expect(wrapper.find('.history-drawer-backdrop').exists()).toBe(true)
    expect(wrapper.get('#history-sidebar').attributes('inert')).toBeUndefined()
    expect(wrapper.get('#history-sidebar').attributes('aria-hidden')).toBeUndefined()
    expect(wrapper.get('.app-shell').attributes('inert')).not.toBeUndefined()

    await wrapper.get('.history-drawer-backdrop').trigger('click')
    await flushPromises()
    expect(wrapper.get('.history-drawer-toggle').attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('.history-drawer-backdrop').exists()).toBe(false)
    expect(wrapper.get('.app-layout').classes()).not.toContain('app-layout--drawer-open')

    await wrapper.get('.history-drawer-toggle').trigger('click')
    await flushPromises()
    expect(wrapper.find('.history-drawer-backdrop').exists()).toBe(true)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.get('.history-drawer-toggle').attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('.history-drawer-backdrop').exists()).toBe(false)
    wrapper.unmount()
  })

  it('uses a fixed viewport shell with isolated left/right overflow and an in-flow composer', async () => {
    stubMatchMedia(false)
    const scripted = makeHistoryAppClient()
    const wrapper = mount(App, {
      props: { client: scripted.client, storage: null },
      attachTo: document.body,
    })
    await settle()

    expect(getComputedStyle(document.documentElement).overflow).toBe('hidden')
    expect(getComputedStyle(document.body).overflow).toBe('hidden')
    expect(getComputedStyle(wrapper.get('.app-layout').element).overflow).toBe('hidden')
    expect(getComputedStyle(wrapper.get('.app-main').element).overflow).toBe('hidden')
    expect(getComputedStyle(wrapper.get('.history-sidebar').element).overflow).toMatch(/auto|scroll/)
    expect(wrapper.find('.composer').exists()).toBe(false)

    await createEmptySession(wrapper)

    expect(getComputedStyle(wrapper.get('.composer').element).position).not.toBe('fixed')
    expect(getComputedStyle(wrapper.get('.conversation-workspace__scroll').element).overflow).toMatch(/auto|scroll/)

    expect(wrapper.get('.conversation-workspace__scroll').element.contains(wrapper.get('.timeline').element)).toBe(true)
    expect(wrapper.get('.conversation-workspace').element.contains(wrapper.get('.composer').element)).toBe(true)
    wrapper.unmount()
  })

  it('closes the narrow drawer after selecting a history session', async () => {
    stubMatchMedia(true)
    const scripted = makeHistoryAppClient()
    const wrapper = mount(App, { props: { client: scripted.client, storage: null } })
    await settle()

    await wrapper.get('.history-drawer-toggle').trigger('click')
    await flushPromises()
    expect(wrapper.get('.app-layout').classes()).toContain('app-layout--drawer-open')

    await wrapper.get('.history-sidebar__select').trigger('click')
    await settle()

    expect(wrapper.get('.history-drawer-toggle').attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('.app-layout').classes()).not.toContain('app-layout--drawer-open')
    wrapper.unmount()
  })
})
