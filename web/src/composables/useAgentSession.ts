import { computed, ref, type ComputedRef, type Ref } from 'vue'

import {
  AgentApiError,
  AgentHttpClient,
  AgentNetworkError,
  AgentRequestAbortedError,
} from '../api/client'
import type { AgentCommand, AgentEventStreamMessage, RuntimeState } from '../domain/protocol'
import {
  CommandGate,
  SessionAction,
  SessionState,
  commandGateFor,
  createInitialSessionState,
  reduceSession,
} from '../domain/reducer'

export const DEFAULT_STORAGE_KEY = 'coding-agent-neo.transport-session'

/**
 * Event-stream reconnect is deliberately finite.  POST commands are never
 * part of this loop; only a failed or ended GET/SSE attachment is retried.
 */
export const SSE_RECONNECT_INITIAL_DELAY_MS = 250
export const SSE_RECONNECT_MAX_DELAY_MS = 5_000
export const SSE_RECONNECT_MAX_ATTEMPTS = 5

export interface EventStreamReconnectOptions {
  readonly initialDelayMs?: number
  readonly maxDelayMs?: number
  readonly maxAttempts?: number
}

export interface PersistedTransportSession {
  readonly transportSessionId: string
  readonly cursor: number
}

export interface UseAgentSessionOptions {
  readonly client?: AgentHttpClient
  readonly storage?: Storage | null
  readonly storageKey?: string
  readonly autoStartEvents?: boolean
  readonly reconnect?: EventStreamReconnectOptions
}

export class SessionCommandError extends Error {
  readonly name = 'SessionCommandError'

  constructor(message: string) {
    super(message)
  }
}

function defaultStorage(): Storage | null {
  try {
    return typeof window !== 'undefined' && window.localStorage !== undefined
      ? window.localStorage
      : null
  } catch {
    return null
  }
}

function validStorageSession(value: unknown): PersistedTransportSession | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }
  const record = value as Record<string, unknown>
  const allowedKeys = new Set(['transportSessionId', 'cursor'])
  if (
    Object.keys(record).some((key) => !allowedKeys.has(key)) ||
    typeof record.transportSessionId !== 'string' ||
    record.transportSessionId.trim().length === 0 ||
    typeof record.cursor !== 'number' ||
    !Number.isSafeInteger(record.cursor) ||
    record.cursor < 0
  ) {
    return null
  }
  return { transportSessionId: record.transportSessionId, cursor: record.cursor }
}

export function loadPersistedTransportSession(
  storage: Storage | null | undefined,
  storageKey = DEFAULT_STORAGE_KEY,
): PersistedTransportSession | null {
  if (storage === null || storage === undefined) {
    return null
  }
  try {
    const serialized = storage.getItem(storageKey)
    if (serialized === null) {
      return null
    }
    return validStorageSession(JSON.parse(serialized) as unknown)
  } catch {
    return null
  }
}

export function savePersistedTransportSession(
  storage: Storage | null | undefined,
  value: PersistedTransportSession,
  storageKey = DEFAULT_STORAGE_KEY,
): void {
  if (storage === null || storage === undefined) {
    return
  }
  if (validStorageSession(value) === null) {
    return
  }
  try {
    // Deliberately persist only the opaque transport ID and the last
    // successfully consumed event cursor.  No event, task, state, config,
    // workspace, or secret is written to browser storage.
    storage.setItem(
      storageKey,
      JSON.stringify({
        transportSessionId: value.transportSessionId,
        cursor: value.cursor,
      }),
    )
  } catch {
    // Private browsing and quota failures must not crash the session.
  }
}

export function clearPersistedTransportSession(
  storage: Storage | null | undefined,
  storageKey = DEFAULT_STORAGE_KEY,
): void {
  if (storage === null || storage === undefined) {
    return
  }
  try {
    storage.removeItem(storageKey)
  } catch {
    // Storage is an optional resume hint, never a session authority.
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof AgentApiError || error instanceof AgentNetworkError) {
    return error.message
  }
  if (error instanceof Error && error.message.trim().length > 0) {
    return error.message.length <= 500 ? error.message : `${error.message.slice(0, 499)}…`
  }
  return 'Agent 请求失败'
}

function isUncertainPostFailure(error: unknown): boolean {
  return error instanceof AgentNetworkError ||
    (error instanceof AgentRequestAbortedError && !(error instanceof AgentApiError))
}

function isUnavailableSessionError(error: unknown): boolean {
  return error instanceof AgentApiError && (
    error.code === 'session_not_found' ||
    error.code === 'session_closed' ||
    error.status === 404 ||
    error.status === 410
  )
}

function safeReconnectDelay(value: number | undefined, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.min(Math.floor(value), 60_000)
    : fallback
}

function safeReconnectAttempts(value: number | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.min(Math.floor(value), 100)
    : SSE_RECONNECT_MAX_ATTEMPTS
}

function stateWith(
  state: Ref<SessionState>,
  action: SessionAction,
): SessionState {
  const next = reduceSession(state.value, action)
  state.value = next
  return next
}

export interface AgentSessionController {
  readonly state: Ref<SessionState>
  readonly gate: ComputedRef<CommandGate>
  readonly transportSessionId: ComputedRef<string | null>
  readonly cursor: ComputedRef<number>
  readonly storedSession: ComputedRef<PersistedTransportSession | null>
  readonly connect: (signal?: AbortSignal) => Promise<SessionState>
  readonly startEvents: () => void
  readonly stopEvents: () => void
  readonly submitTask: (text: string, signal?: AbortSignal) => Promise<void>
  readonly respondToApproval: (
    requestId: string,
    approved: boolean,
    signal?: AbortSignal,
  ) => Promise<void>
  readonly interrupt: (reason?: string, signal?: AbortSignal) => Promise<void>
  readonly close: (reason?: string, signal?: AbortSignal) => Promise<void>
  readonly deleteSession: (signal?: AbortSignal) => Promise<void>
  readonly forgetSession: (message?: string) => void
  readonly dispatch: (action: SessionAction) => SessionState
}

export function useAgentSession(options: UseAgentSessionOptions = {}): AgentSessionController {
  const client = options.client ?? new AgentHttpClient()
  const storage = options.storage === undefined ? defaultStorage() : options.storage
  const storageKey = options.storageKey ?? DEFAULT_STORAGE_KEY
  const initialStored = loadPersistedTransportSession(storage, storageKey)
  const storedHint = ref<PersistedTransportSession | null>(initialStored)
  const state = ref<SessionState>(
    createInitialSessionState(
      initialStored?.transportSessionId ?? null,
      initialStored?.cursor ?? 0,
    ),
  )
  const gate = computed(() => commandGateFor(state.value))
  const transportSessionId = computed(() => state.value.transportSessionId)
  const cursor = computed(() => state.value.cursor)
  const storedSession = computed(() => storedHint.value)
  let eventsAbortController: AbortController | null = null
  let eventsRun = 0
  let reconnectTimer: ReturnType<typeof globalThis.setTimeout> | null = null
  let reconnectAttempt = 0
  let reconnectAfterStream = false
  const reconnectInitialDelay = safeReconnectDelay(
    options.reconnect?.initialDelayMs,
    SSE_RECONNECT_INITIAL_DELAY_MS,
  )
  const reconnectMaxDelay = safeReconnectDelay(
    options.reconnect?.maxDelayMs,
    SSE_RECONNECT_MAX_DELAY_MS,
  )
  const reconnectMaxAttempts = safeReconnectAttempts(options.reconnect?.maxAttempts)

  function dispatch(action: SessionAction): SessionState {
    const before = state.value.cursor
    const next = stateWith(state, action)
    // A duplicate or a gap intentionally leaves the cursor unchanged.  Only
    // an event accepted by the pure reducer advances browser persistence.
    if (action.type === 'EVENT' && next.cursor > before) {
      persistCursor()
    }
    if (
      (action.type === 'EVENT' && next.connection === 'closed' && next.lastEvent?.type === 'session_end') ||
      action.type === 'CLOSED' ||
      action.type === 'SESSION_UNAVAILABLE' ||
      action.type === 'RESET'
    ) {
      clearStoredHint()
    }
    return next
  }

  function persistCursor(): void {
    const id = state.value.transportSessionId
    if (id !== null) {
      const value = { transportSessionId: id, cursor: state.value.cursor }
      storedHint.value = value
      savePersistedTransportSession(storage, value, storageKey)
    }
  }

  function clearStoredHint(): void {
    storedHint.value = null
    clearPersistedTransportSession(storage, storageKey)
  }

  function handleEvent(message: AgentEventStreamMessage): boolean {
    const before = state.value.cursor
    dispatch({ type: 'EVENT', event: message.data })
    return state.value.cursor > before
  }

  function clearReconnectTimer(): void {
    if (reconnectTimer !== null) {
      globalThis.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  function stopEvents(): void {
    clearReconnectTimer()
    reconnectAttempt = 0
    reconnectAfterStream = false
    // Invalidate callbacks from a stream which is still unwinding after an
    // abort.  Aborting a GET is not evidence that the Agent session ended.
    eventsRun += 1
    eventsAbortController?.abort()
    eventsAbortController = null
  }

  function forgetSession(message = '保存的 Agent session 已不存在或已关闭，请新建 session。'): void {
    stopEvents()
    clearStoredHint()
    dispatch({ type: 'SESSION_UNAVAILABLE', message })
  }

  type ReconnectReason = 'stream_failure' | 'sequence_gap'

  function scheduleReconnect(reason: ReconnectReason): void {
    const id = state.value.transportSessionId
    if (id === null || state.value.connection === 'closed') {
      return
    }
    // A sequence gap has to be repaired from the unchanged cursor as soon as
    // the current GET unwinds.  It does not consume a retry budget.
    if (reason === 'sequence_gap') {
      clearReconnectTimer()
      reconnectAfterStream = true
      if (eventsAbortController !== null) {
        return
      }
      reconnectAfterStream = false
      if (reconnectAttempt >= reconnectMaxAttempts) {
        dispatch({
          type: 'STREAM_RETRY_EXHAUSTED',
          message: '事件流跳号修复次数已达上限，请手动重新连接。',
        })
        return
      }
      reconnectAttempt += 1
    } else {
      if (reconnectTimer !== null) {
        return
      }
      if (reconnectAttempt >= reconnectMaxAttempts) {
        dispatch({
          type: 'STREAM_RETRY_EXHAUSTED',
          message: '事件流重连次数已达上限，请手动重新连接。',
        })
        return
      }
    }

    const attempt = reconnectAttempt
    if (reason === 'stream_failure') {
      reconnectAttempt += 1
    }
    const delay = reason === 'sequence_gap'
      ? 0
      : Math.min(reconnectMaxDelay, reconnectInitialDelay * (2 ** Math.min(attempt, 30)))
    const run = (): void => {
      reconnectTimer = null
      if (state.value.connection === 'closed' || state.value.transportSessionId === null) {
        return
      }
      // Let the aborted generator finish before opening the replacement GET.
      if (eventsAbortController !== null) {
        return
      }
      startEventAttempt(false)
    }
    reconnectTimer = globalThis.setTimeout(run, delay)
  }

  function startEventAttempt(resetBackoff: boolean): void {
    const id = state.value.transportSessionId
    if (id === null || eventsAbortController !== null || state.value.connection === 'closed') {
      return
    }
    if (resetBackoff) {
      reconnectAttempt = 0
      reconnectAfterStream = false
    }
    const controller = new AbortController()
    eventsAbortController = controller
    const run = ++eventsRun
    void client
      .streamEvents(id, state.value.cursor, {
        onOpen: () => {
          if (eventsRun === run && !controller.signal.aborted) {
            dispatch({ type: 'STREAM_OPENED' })
          }
        },
        onEvent: (message) => {
          if (
            eventsRun !== run ||
            controller.signal.aborted ||
            state.value.connection === 'closed'
          ) {
            return
          }
          const advanced = handleEvent(message)
          if (advanced) {
            // A useful event proves that this attachment is making progress;
            // the next independent connection failure may start at backoff 0.
            reconnectAttempt = 0
          }
          if (state.value.needsResubscribe && eventsRun === run) {
            controller.abort()
            scheduleReconnect('sequence_gap')
          }
        },
      }, controller.signal)
      .then(() => {
        if (
          eventsRun === run &&
          !controller.signal.aborted &&
          state.value.connection !== 'closed'
        ) {
          dispatch({ type: 'STREAM_CLOSED' })
          scheduleReconnect('stream_failure')
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || eventsRun !== run) {
          return
        }
        if (isUnavailableSessionError(error)) {
          forgetSession()
          return
        }
        dispatch({ type: 'STREAM_ERROR', message: errorMessage(error) })
        // HTTP status/protocol failures are not connection failures.  A
        // network error or an abruptly broken stream is safe to retry as GET.
        if (error instanceof AgentNetworkError || !(error instanceof AgentApiError)) {
          scheduleReconnect('stream_failure')
        }
      })
      .finally(() => {
        if (eventsRun === run) {
          eventsAbortController = null
          if (reconnectAfterStream && state.value.connection !== 'closed') {
            reconnectAfterStream = false
            scheduleReconnect('sequence_gap')
          }
        }
      })
  }

  function startEvents(): void {
    clearReconnectTimer()
    if (state.value.connection === 'error' && storedHint.value !== null) {
      // A manual retry after the finite budget still follows the refresh
      // rule: query the persisted session before opening another SSE GET.
      void connect().catch(() => undefined)
      return
    }
    startEventAttempt(true)
  }

  async function connect(signal?: AbortSignal): Promise<SessionState> {
    if (state.value.connection === 'connecting') {
      throw new SessionCommandError('连接 Agent 中')
    }
    if (state.value.connection === 'connected' && state.value.transportSessionId !== null) {
      return state.value
    }
    const resumeHint = state.value.connection === 'closed' ? null : storedHint.value
    if (state.value.connection === 'closed' && resumeHint === null) {
      // A closed session has no valid resume authority.  Start a fresh
      // projection before creating the next transport session.
      dispatch({ type: 'RESET' })
    }
    dispatch({ type: 'CONNECTING' })
    try {
      // A browser-provided ID is only a resume hint.  Query it first; never
      // create a second session or claim that a missing server session has
      // historical recovery support.
      if (resumeHint !== null) {
        const status = await client.getSession(resumeHint.transportSessionId, signal)
        if (status.closed) {
          throw new AgentApiError(410, 'session_closed', 'transport session is closed')
        }
        dispatch({
          type: 'SESSION_ATTACHED',
          transportSessionId: resumeHint.transportSessionId,
          // Keep the last cursor acknowledged by this browser.  The server's
          // latest cursor is intentionally not used to skip replayable facts.
          cursor: resumeHint.cursor,
          state: status.state,
        })
        storedHint.value = resumeHint
        savePersistedTransportSession(storage, resumeHint, storageKey)
      } else {
        const created = await client.createSession(signal)
        dispatch({
          type: 'CONNECTED',
          transportSessionId: created.transport_session_id,
          cursor: created.cursor,
          state: created.state,
        })
        const value = {
          transportSessionId: created.transport_session_id,
          cursor: created.cursor,
        }
        storedHint.value = value
        savePersistedTransportSession(storage, value, storageKey)
      }
      if (options.autoStartEvents !== false) {
        startEvents()
      }
      return state.value
    } catch (error) {
      if (isUnavailableSessionError(error)) {
        forgetSession()
      } else {
        dispatch({ type: 'STREAM_ERROR', message: errorMessage(error) })
      }
      throw error
    }
  }

  function ensureSession(): string {
    const id = state.value.transportSessionId
    if (id === null || state.value.connection !== 'connected') {
      throw new SessionCommandError('session 尚未连接或已关闭')
    }
    return id
  }

  function ensureCan(kind: keyof Pick<CommandGate, 'canSubmitTask' | 'canRespondToApproval' | 'canInterrupt'>): void {
    if (!gate.value[kind]) {
      throw new SessionCommandError(gate.value.reason)
    }
  }

  async function send(
    command: AgentCommand,
    signal?: AbortSignal,
  ): Promise<void> {
    const id = ensureSession()
    dispatch({ type: 'COMMAND_STARTED', commandType: command.type })
    try {
      await client.sendCommand(id, command, signal)
      dispatch({ type: 'COMMAND_ACCEPTED', commandType: command.type })
    } catch (error) {
      dispatch({
        type: 'COMMAND_FAILED',
        commandType: command.type,
        message: errorMessage(error),
        uncertain: isUncertainPostFailure(error),
      })
      if (isUnavailableSessionError(error)) {
        forgetSession()
      }
      throw error
    }
  }

  async function submitTask(text: string, signal?: AbortSignal): Promise<void> {
    if (typeof text !== 'string' || text.trim().length === 0) {
      throw new SessionCommandError('任务不能为空')
    }
    ensureCan('canSubmitTask')
    await send({ type: 'SubmitTask', text }, signal)
  }

  async function respondToApproval(
    requestId: string,
    approved: boolean,
    signal?: AbortSignal,
  ): Promise<void> {
    if (
      typeof requestId !== 'string' ||
      requestId.trim().length === 0 ||
      state.value.pendingApproval?.requestId !== requestId
    ) {
      throw new SessionCommandError('授权请求 ID 无效')
    }
    if (typeof approved !== 'boolean') {
      throw new SessionCommandError('授权决定无效')
    }
    ensureCan('canRespondToApproval')
    await send({ type: 'ApprovalResponse', request_id: requestId, approved }, signal)
  }

  async function interrupt(reason = 'user_cancelled', signal?: AbortSignal): Promise<void> {
    ensureCan('canInterrupt')
    if (typeof reason !== 'string' || reason.trim().length === 0) {
      throw new SessionCommandError('中断原因不能为空')
    }
    await send({ type: 'Interrupt', reason }, signal)
  }

  async function close(reason = 'frontend_exit', signal?: AbortSignal): Promise<void> {
    if (!gate.value.canClose) {
      throw new SessionCommandError(gate.value.reason)
    }
    if (typeof reason !== 'string' || reason.trim().length === 0) {
      throw new SessionCommandError('关闭原因不能为空')
    }
    await send({ type: 'CloseSession', reason }, signal)
    stopEvents()
    dispatch({ type: 'CLOSED' })
    clearStoredHint()
  }

  async function deleteSession(signal?: AbortSignal): Promise<void> {
    const id = ensureSession()
    try {
      await client.deleteSession(id, signal)
    } catch (error) {
      if (isUnavailableSessionError(error)) {
        forgetSession()
      }
      throw error
    }
    stopEvents()
    dispatch({ type: 'CLOSED' })
    clearStoredHint()
  }

  return {
    state,
    gate,
    transportSessionId,
    cursor,
    storedSession,
    connect,
    startEvents,
    stopEvents,
    submitTask,
    respondToApproval,
    interrupt,
    close,
    deleteSession,
    forgetSession,
    dispatch,
  }
}

export type { RuntimeState }
