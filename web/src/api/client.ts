import {
  AgentCommand,
  AgentCommandType,
  AgentEventStreamMessage,
  AcceptedResponse,
  BASE_PATH,
  HealthResponse,
  PROTOCOL_VERSION,
  RUNTIME_STATES,
  RuntimeState,
  SessionCreatedResponse,
  SessionStatusResponse,
  asTransportSessionId,
  isApprovalMode,
  isNonNegativeInteger,
  isRecord,
  isRuntimeState,
} from '../domain/protocol'
import type { CanonicalSessionId } from '../domain/protocol'
import {
  isCanonicalSessionId,
  isHistoryEventLimit,
  isHistoryListLimit,
  isHistorySince,
  isOpaqueHistoryListCursor,
  parseSessionEventPage,
  parseSessionHistoryPage,
} from '../domain/history'
import type { SessionEventPage, SessionHistoryPage } from '../domain/history'

export type ApiErrorCode =
  | 'invalid_host'
  | 'invalid_origin'
  | 'invalid_session_request'
  | 'invalid_cursor'
  | 'invalid_command'
  | 'invalid_history_id'
  | 'invalid_history_cursor'
  | 'invalid_history_limit'
  | 'session_not_found'
  | 'history_not_found'
  | 'session_exists'
  | 'turn_in_progress'
  | 'history_unavailable'
  | 'invalid_resume'
  | 'session_closed'
  | 'internal_error'
  | 'network_error'
  | 'aborted'
  | 'protocol_error'

const KNOWN_API_ERROR_CODES: readonly ApiErrorCode[] = [
  'invalid_host',
  'invalid_origin',
  'invalid_session_request',
  'invalid_cursor',
  'invalid_command',
  'invalid_history_id',
  'invalid_history_cursor',
  'invalid_history_limit',
  'session_not_found',
  'history_not_found',
  'session_exists',
  'turn_in_progress',
  'history_unavailable',
  'invalid_resume',
  'session_closed',
  'internal_error',
]

const CLIENT_OWNED_ERROR_MESSAGES: Partial<Record<ApiErrorCode, string>> = {
  invalid_history_id: 'history session ID is invalid',
  invalid_history_cursor: 'history cursor is invalid',
  invalid_history_limit: 'history limit is invalid',
  history_not_found: 'session history was not found',
  history_unavailable: 'session history is unavailable',
  invalid_resume: 'session cannot be resumed',
  session_exists: 'an active transport session already exists',
}

export class AgentApiError extends Error {
  readonly name: string = 'AgentApiError'

  constructor(
    readonly status: number,
    readonly code: ApiErrorCode,
    message: string,
  ) {
    super(message)
  }
}

export class AgentNetworkError extends Error {
  readonly name = 'AgentNetworkError'
  readonly code = 'network_error' as const

  constructor(message = '无法连接 Agent 服务') {
    super(message)
  }
}

export class AgentRequestAbortedError extends Error {
  readonly name = 'AgentRequestAbortedError'
  readonly code = 'aborted' as const

  constructor() {
    super('请求已取消')
  }
}

export class AgentProtocolError extends AgentApiError {
  readonly name = 'AgentProtocolError'

  constructor(message = 'Agent 服务返回了无法识别的响应') {
    super(500, 'protocol_error', message)
  }
}

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>

export interface AgentHttpClientOptions {
  readonly baseUrl?: string
  readonly fetchImpl?: FetchLike
}

export interface EventStreamHandlers {
  /** Called after the HTTP response and stream body have been validated. */
  readonly onOpen?: () => void
  readonly onEvent?: (message: AgentEventStreamMessage) => void
}

export interface ListSessionHistoryOptions {
  readonly limit?: number
  readonly cursor?: string
  readonly signal?: AbortSignal
}

export interface ReadSessionHistoryEventsOptions {
  readonly since?: number
  readonly limit?: number
  readonly signal?: AbortSignal
}

interface RequestOptions {
  readonly method: 'GET' | 'POST' | 'DELETE'
  readonly path: string
  readonly body?: unknown
  readonly signal?: AbortSignal
  readonly headers?: HeadersInit
  readonly fallbackCode?: ApiErrorCode
}

const DEFAULT_ERROR_MESSAGE = 'Agent 服务请求失败'
function defaultFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  if (typeof globalThis.fetch !== 'function') {
    return Promise.reject(new AgentNetworkError('当前环境不支持 fetch'))
  }
  return globalThis.fetch(input, init)
}

function trimBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, '')
}

function requestUrl(baseUrl: string, path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path
  }
  if (baseUrl.length === 0) {
    return path
  }
  return `${baseUrl}${path.startsWith('/') ? path : `/${path}`}`
}

function fallbackErrorCode(status: number, fallback: ApiErrorCode | undefined): ApiErrorCode {
  if (fallback !== undefined) {
    return fallback
  }
  if (status === 404) {
    return 'session_not_found'
  }
  if (status === 409) {
    return 'internal_error'
  }
  if (status === 410) {
    return 'session_closed'
  }
  return 'internal_error'
}

function safeErrorMessage(value: unknown, fallback = DEFAULT_ERROR_MESSAGE): string {
  if (typeof value !== 'string' || value.trim().length === 0) {
    return fallback
  }
  return value.length <= 500 ? value : `${value.slice(0, 499)}…`
}

async function responseJson(response: Response): Promise<unknown> {
  const candidate = response as Response & {
    json?: () => Promise<unknown>
    text?: () => Promise<string>
  }
  if (typeof candidate.json === 'function') {
    try {
      return await candidate.json()
    } catch {
      return null
    }
  }
  try {
    const text = typeof candidate.text === 'function' ? await candidate.text() : ''
    if (text.trim().length === 0) {
      return null
    }
    return JSON.parse(text) as unknown
  } catch {
    return null
  }
}

function ownedErrorMessage(code: ApiErrorCode): string | undefined {
  return CLIENT_OWNED_ERROR_MESSAGES[code]
}

function responseError(
  status: number,
  body: unknown,
  fallbackCode: ApiErrorCode | undefined,
): AgentApiError {
  const fallback = fallbackErrorCode(status, fallbackCode)
  if (isRecord(body) && isRecord(body.error)) {
    const rawCode = body.error.code
    const known =
      typeof rawCode === 'string' && (KNOWN_API_ERROR_CODES as readonly string[]).includes(rawCode)
    if (known) {
      const code = rawCode as ApiErrorCode
      const owned = ownedErrorMessage(code)
      if (owned !== undefined) {
        return new AgentApiError(status, code, owned)
      }
      return new AgentApiError(
        status,
        code,
        safeErrorMessage(body.error.message, DEFAULT_ERROR_MESSAGE),
      )
    }
    return new AgentApiError(status, fallback, DEFAULT_ERROR_MESSAGE)
  }
  return new AgentApiError(status, fallback, DEFAULT_ERROR_MESSAGE)
}

function historyClientError(
  code: 'invalid_history_id' | 'invalid_history_cursor' | 'invalid_history_limit',
): AgentApiError {
  return new AgentApiError(400, code, CLIENT_OWNED_ERROR_MESSAGES[code] ?? DEFAULT_ERROR_MESSAGE)
}

function isAbortSignal(value: unknown): value is AbortSignal {
  return typeof AbortSignal !== 'undefined' && value instanceof AbortSignal
}

function queryString(params: ReadonlyArray<readonly [string, string]>): string {
  if (params.length === 0) {
    return ''
  }
  return `?${params
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join('&')}`
}

function isSuccessful(response: Response): boolean {
  return response.status >= 200 && response.status < 300
}

function requireStatus(response: Response, expected: number): Response {
  if (response.status !== expected) {
    throw new AgentProtocolError(`Agent 服务返回了非预期的 HTTP ${expected} 响应`)
  }
  return response
}

function parseHealth(body: unknown): HealthResponse {
  if (
    !isRecord(body) ||
    body.status !== 'ok' ||
    body.protocol_version !== PROTOCOL_VERSION
  ) {
    throw new AgentProtocolError('health 响应的 protocol_version 无法确认')
  }
  return { status: 'ok', protocol_version: PROTOCOL_VERSION }
}

function parseCreated(body: unknown): SessionCreatedResponse {
  if (
    !isRecord(body) ||
    typeof body.transport_session_id !== 'string' ||
    body.transport_session_id.trim().length === 0 ||
    !isRuntimeState(body.state) ||
    !isNonNegativeInteger(body.cursor) ||
    (body.approval_mode !== undefined && !isApprovalMode(body.approval_mode))
  ) {
    throw new AgentProtocolError('session 创建响应不符合 transport binding')
  }
  return {
    transport_session_id: asTransportSessionId(body.transport_session_id),
    state: body.state,
    cursor: body.cursor,
    approval_mode: isApprovalMode(body.approval_mode) ? body.approval_mode : 'ask',
  }
}

function parseStatus(body: unknown): SessionStatusResponse {
  if (
    !isRecord(body) ||
    !isRuntimeState(body.state) ||
    !isNonNegativeInteger(body.cursor) ||
    typeof body.closed !== 'boolean'
    || (body.approval_mode !== undefined && !isApprovalMode(body.approval_mode))
  ) {
    throw new AgentProtocolError('session 状态响应不符合 transport binding')
  }
  return {
    state: body.state,
    cursor: body.cursor,
    closed: body.closed,
    approval_mode: isApprovalMode(body.approval_mode) ? body.approval_mode : 'ask',
  }
}

function parseAccepted(body: unknown): AcceptedResponse {
  if (!isRecord(body) || body.accepted !== true) {
    throw new AgentProtocolError('command 响应不符合 transport binding')
  }
  return { accepted: true }
}

function commandFields(command: AgentCommand): string[] {
  return Object.keys(command)
}

export function validateCommand(command: AgentCommand): void {
  if (!isRecord(command) || typeof command.type !== 'string') {
    throw new AgentApiError(400, 'invalid_command', 'command is invalid')
  }
  const fields = commandFields(command)
  const expected: Record<AgentCommandType, {
    readonly required: readonly string[]
    readonly optional: readonly string[]
  }> = {
    SubmitTask: { required: ['type', 'text'], optional: [] },
    ApprovalResponse: { required: ['type', 'request_id', 'approved'], optional: [] },
    SetApprovalMode: { required: ['type', 'mode'], optional: [] },
    Interrupt: { required: ['type'], optional: ['reason'] },
    CloseSession: { required: ['type'], optional: ['reason'] },
  }
  if (!(command.type in expected)) {
    throw new AgentApiError(400, 'invalid_command', 'command is invalid')
  }
  const expectedFields = expected[command.type]
  const allowedFields = [...expectedFields.required, ...expectedFields.optional]
  const hasExactFields =
    expectedFields.required.every((field) => fields.includes(field)) &&
    fields.every((field) => allowedFields.includes(field))
  if (!hasExactFields) {
    throw new AgentApiError(400, 'invalid_command', 'command is invalid')
  }
  if (command.type === 'SubmitTask' && (typeof command.text !== 'string' || command.text.trim().length === 0)) {
    throw new AgentApiError(400, 'invalid_command', 'command is invalid')
  }
  if (
    command.type === 'SetApprovalMode' && !isApprovalMode(command.mode)
  ) {
    throw new AgentApiError(400, 'invalid_command', 'command is invalid')
  }
  if (
    command.type === 'ApprovalResponse' &&
    (typeof command.request_id !== 'string' ||
      command.request_id.trim().length === 0 ||
      typeof command.approved !== 'boolean')
  ) {
    throw new AgentApiError(400, 'invalid_command', 'command is invalid')
  }
  if (
    (command.type === 'Interrupt' || command.type === 'CloseSession') &&
    'reason' in command &&
    (typeof command.reason !== 'string' || command.reason.trim().length === 0)
  ) {
    throw new AgentApiError(400, 'invalid_command', 'command is invalid')
  }
}

function wireCommand(command: AgentCommand): Record<string, unknown> {
  validateCommand(command)
  switch (command.type) {
    case 'SubmitTask':
      return { type: command.type, text: command.text }
    case 'ApprovalResponse':
      return { type: command.type, request_id: command.request_id, approved: command.approved }
    case 'SetApprovalMode':
      return { type: command.type, mode: command.mode }
    case 'Interrupt':
      return 'reason' in command ? { type: command.type, reason: command.reason } : { type: command.type }
    case 'CloseSession':
      return 'reason' in command ? { type: command.type, reason: command.reason } : { type: command.type }
  }
}

function parseSseLine(
  line: string,
  frame: { id: string | null; event: string; data: string[] },
): void {
  if (line.startsWith(':')) {
    return
  }
  const separator = line.indexOf(':')
  const field = separator < 0 ? line : line.slice(0, separator)
  let value = separator < 0 ? '' : line.slice(separator + 1)
  if (value.startsWith(' ')) {
    value = value.slice(1)
  }
  if (field === 'id') {
    frame.id = value
  } else if (field === 'event') {
    frame.event = value
  } else if (field === 'data') {
    frame.data.push(value)
  }
}

function dispatchSseFrame(
  frame: { id: string | null; event: string; data: string[] },
): AgentEventStreamMessage | null {
  if (frame.data.length === 0) {
    return null
  }
  const rawData = frame.data.join('\n')
  let data: unknown
  try {
    data = JSON.parse(rawData) as unknown
  } catch {
    data = null
  }
  const message: AgentEventStreamMessage = {
    id: frame.id,
    event: frame.event,
    data,
    rawData,
  }
  frame.id = null
  frame.event = ''
  frame.data = []
  return message
}

async function* sseMessages(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<AgentEventStreamMessage> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const frame = { id: null as string | null, event: '', data: [] as string[] }
  const cancelReader = (): void => {
    void reader.cancel().catch(() => undefined)
  }
  signal?.addEventListener('abort', cancelReader, { once: true })
  try {
    while (true) {
      if (signal?.aborted) {
        throw new AgentRequestAbortedError()
      }
      const chunk = await reader.read()
      if (signal?.aborted) {
        throw new AgentRequestAbortedError()
      }
      if (chunk.done) {
        buffer += decoder.decode()
        if (buffer.length > 0) {
          const lines = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n')
          buffer = ''
          for (const line of lines) {
            if (line.length === 0) {
              const message = dispatchSseFrame(frame)
              if (message !== null && message.event === 'agent-event') {
                yield message
              }
            } else {
              parseSseLine(line, frame)
            }
          }
        }
        const finalMessage = dispatchSseFrame(frame)
        if (finalMessage !== null && finalMessage.event === 'agent-event') {
          yield finalMessage
        }
        return
      }
      buffer += decoder.decode(chunk.value, { stream: true })
      const normalized = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
      const lines = normalized.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (line.length === 0) {
          const message = dispatchSseFrame(frame)
          if (message !== null && message.event === 'agent-event') {
            yield message
          }
        } else {
          parseSseLine(line, frame)
        }
      }
    }
  } finally {
    signal?.removeEventListener('abort', cancelReader)
    try {
      await reader.cancel()
    } catch {
      // The caller owns the AbortController; a cancelled stream is terminal.
    }
  }
}

/** Exported for parser contract tests without requiring a live HTTP server. */
export async function* parseSseStream(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<AgentEventStreamMessage> {
  yield* sseMessages(body, signal)
}

export class AgentHttpClient {
  readonly baseUrl: string
  private readonly fetchImpl: FetchLike

  constructor(options: AgentHttpClientOptions = {}) {
    this.baseUrl = trimBaseUrl(options.baseUrl ?? '')
    this.fetchImpl = options.fetchImpl ?? defaultFetch
  }

  private async request(options: RequestOptions): Promise<Response> {
    const headers = new Headers(options.headers)
    if (!headers.has('Accept')) {
      headers.set('Accept', 'application/json')
    }
    const init: RequestInit = {
      method: options.method,
      headers,
      credentials: 'same-origin',
      signal: options.signal,
    }
    if (options.body !== undefined) {
      headers.set('Content-Type', 'application/json')
      init.body = JSON.stringify(options.body)
    }
    let response: Response
    try {
      response = await this.fetchImpl(requestUrl(this.baseUrl, options.path), init)
    } catch (error) {
      if (options.signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
        throw new AgentRequestAbortedError()
      }
      throw error instanceof AgentNetworkError ? error : new AgentNetworkError()
    }
    if (!isSuccessful(response)) {
      throw responseError(response.status, await responseJson(response), options.fallbackCode)
    }
    return response
  }

  async health(signal?: AbortSignal): Promise<HealthResponse> {
    const response = await this.request({ method: 'GET', path: `${BASE_PATH}/health`, signal })
    return parseHealth(await responseJson(requireStatus(response, 200)))
  }

  /**
   * Create a new transport session, or resume a canonical history session.
   *
   * The first argument may be an `AbortSignal` so existing callers that pass
   * only a signal keep sending `{}`. POST is never automatically replayed.
   */
  async createSession(
    resumeSessionIdOrSignal?: CanonicalSessionId | string | AbortSignal,
    signal?: AbortSignal,
  ): Promise<SessionCreatedResponse> {
    let resumeSessionId: string | undefined
    let abortSignal = signal
    if (isAbortSignal(resumeSessionIdOrSignal)) {
      abortSignal = resumeSessionIdOrSignal
    } else if (resumeSessionIdOrSignal !== undefined) {
      resumeSessionId = resumeSessionIdOrSignal
    }

    const body =
      resumeSessionId === undefined
        ? {}
        : { resume_session_id: this.requireHistorySessionId(resumeSessionId) }
    const response = await this.request({
      method: 'POST',
      path: `${BASE_PATH}/sessions`,
      body,
      signal: abortSignal,
      fallbackCode: resumeSessionId === undefined ? 'invalid_session_request' : 'invalid_history_id',
    })
    return parseCreated(await responseJson(requireStatus(response, 201)))
  }

  async listSessionHistory(options: ListSessionHistoryOptions = {}): Promise<SessionHistoryPage> {
    const params: Array<readonly [string, string]> = []
    if (options.limit !== undefined) {
      if (!isHistoryListLimit(options.limit)) {
        throw historyClientError('invalid_history_limit')
      }
      params.push(['limit', String(options.limit)])
    }
    if (options.cursor !== undefined) {
      if (!isOpaqueHistoryListCursor(options.cursor)) {
        throw historyClientError('invalid_history_cursor')
      }
      params.push(['cursor', options.cursor])
    }
    const response = await this.request({
      method: 'GET',
      path: `${BASE_PATH}/session-history${queryString(params)}`,
      signal: options.signal,
      fallbackCode: 'invalid_history_limit',
    })
    const body = await responseJson(requireStatus(response, 200))
    if (!isRecord(body)) {
      throw new AgentProtocolError('session history 响应不符合 transport binding')
    }
    return parseSessionHistoryPage(body)
  }

  async readSessionHistoryEvents(
    sessionId: CanonicalSessionId | string,
    options: ReadSessionHistoryEventsOptions = {},
  ): Promise<SessionEventPage> {
    const id = this.requireHistorySessionId(sessionId)
    const params: Array<readonly [string, string]> = []
    if (options.since !== undefined) {
      if (!isHistorySince(options.since)) {
        throw historyClientError('invalid_history_cursor')
      }
      params.push(['since', String(options.since)])
    }
    if (options.limit !== undefined) {
      if (!isHistoryEventLimit(options.limit)) {
        throw historyClientError('invalid_history_limit')
      }
      params.push(['limit', String(options.limit)])
    }
    const response = await this.request({
      method: 'GET',
      path: `${BASE_PATH}/session-history/${encodeURIComponent(id)}/events${queryString(params)}`,
      signal: options.signal,
      fallbackCode: 'history_not_found',
    })
    const body = await responseJson(requireStatus(response, 200))
    if (!isRecord(body)) {
      throw new AgentProtocolError('session history events 响应不符合 transport binding')
    }
    return parseSessionEventPage(body)
  }

  async getSession(transportSessionId: string, signal?: AbortSignal): Promise<SessionStatusResponse> {
    const id = this.requireSessionId(transportSessionId)
    const response = await this.request({
      method: 'GET',
      path: `${BASE_PATH}/sessions/${encodeURIComponent(id)}`,
      signal,
      fallbackCode: 'session_not_found',
    })
    return parseStatus(await responseJson(requireStatus(response, 200)))
  }

  async sendCommand(
    transportSessionId: string,
    command: AgentCommand,
    signal?: AbortSignal,
  ): Promise<AcceptedResponse> {
    const id = this.requireSessionId(transportSessionId)
    const response = await this.request({
      method: 'POST',
      path: `${BASE_PATH}/sessions/${encodeURIComponent(id)}/commands`,
      body: wireCommand(command),
      signal,
      fallbackCode: 'invalid_command',
    })
    return parseAccepted(await responseJson(requireStatus(response, 202)))
  }

  async deleteSession(transportSessionId: string, signal?: AbortSignal): Promise<void> {
    const id = this.requireSessionId(transportSessionId)
    const response = await this.request({
      method: 'DELETE',
      path: `${BASE_PATH}/sessions/${encodeURIComponent(id)}`,
      signal,
      fallbackCode: 'session_not_found',
    })
    requireStatus(response, 204)
  }

  /**
   * Read the one-way SSE stream from a cursor.  This method never retries a
   * request and never replays a POST command; callers decide if/when a GET
   * should be reattached after a disconnect.
   */
  async *events(
    transportSessionId: string,
    cursor = 0,
    signal?: AbortSignal,
    onOpen?: () => void,
  ): AsyncGenerator<AgentEventStreamMessage> {
    const id = this.requireSessionId(transportSessionId)
    if (!isNonNegativeInteger(cursor)) {
      throw new AgentApiError(400, 'invalid_cursor', 'event cursor is invalid')
    }
    const response = await this.request({
      method: 'GET',
      path: `${BASE_PATH}/sessions/${encodeURIComponent(id)}/events?since=${cursor}`,
      signal,
      headers: {
        Accept: 'text/event-stream',
        'Last-Event-ID': String(cursor),
        'Cache-Control': 'no-cache',
      },
      fallbackCode: 'invalid_cursor',
    })
    requireStatus(response, 200)
    if (response.body === null) {
      throw new AgentProtocolError('SSE 响应缺少 event stream body')
    }
    onOpen?.()
    yield* sseMessages(response.body, signal)
  }

  async streamEvents(
    transportSessionId: string,
    cursor = 0,
    handlers: EventStreamHandlers = {},
    signal?: AbortSignal,
  ): Promise<void> {
    for await (const message of this.events(transportSessionId, cursor, signal, handlers.onOpen)) {
      handlers.onEvent?.(message)
    }
  }

  private requireSessionId(transportSessionId: string): string {
    if (typeof transportSessionId !== 'string' || transportSessionId.trim().length === 0) {
      throw new AgentApiError(400, 'session_not_found', 'transport session id is invalid')
    }
    return transportSessionId
  }

  private requireHistorySessionId(sessionId: CanonicalSessionId | string): CanonicalSessionId {
    if (!isCanonicalSessionId(sessionId)) {
      throw historyClientError('invalid_history_id')
    }
    return sessionId
  }
}

export function createAgentHttpClient(options: AgentHttpClientOptions = {}): AgentHttpClient {
  return new AgentHttpClient(options)
}

export { RUNTIME_STATES, isRuntimeState }
export type { AgentCommand, AgentCommandType, RuntimeState }
