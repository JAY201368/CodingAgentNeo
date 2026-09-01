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

export interface PersistedTransportSession {
  readonly transportSessionId: string
  readonly cursor: number
}

export interface UseAgentSessionOptions {
  readonly client?: AgentHttpClient
  readonly storage?: Storage | null
  readonly storageKey?: string
  readonly autoStartEvents?: boolean
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
  readonly dispatch: (action: SessionAction) => SessionState
}

export function useAgentSession(options: UseAgentSessionOptions = {}): AgentSessionController {
  const client = options.client ?? new AgentHttpClient()
  const storage = options.storage === undefined ? defaultStorage() : options.storage
  const storageKey = options.storageKey ?? DEFAULT_STORAGE_KEY
  const initialStored = loadPersistedTransportSession(storage, storageKey)
  const state = ref<SessionState>(
    createInitialSessionState(
      initialStored?.transportSessionId ?? null,
      initialStored?.cursor ?? 0,
    ),
  )
  const gate = computed(() => commandGateFor(state.value))
  const transportSessionId = computed(() => state.value.transportSessionId)
  const cursor = computed(() => state.value.cursor)
  const storedSession = computed(() => {
    const id = state.value.transportSessionId
    return id === null ? initialStored : { transportSessionId: id, cursor: state.value.cursor }
  })
  let eventsAbortController: AbortController | null = null
  let eventsRun = 0

  function dispatch(action: SessionAction): SessionState {
    const before = state.value.cursor
    const next = stateWith(state, action)
    // A duplicate or a gap intentionally leaves the cursor unchanged.  Only
    // an event accepted by the pure reducer advances browser persistence.
    if (action.type === 'EVENT' && next.cursor > before) {
      persistCursor()
    }
    return next
  }

  function persistCursor(): void {
    const id = state.value.transportSessionId
    if (id !== null) {
      savePersistedTransportSession(storage, { transportSessionId: id, cursor: state.value.cursor }, storageKey)
    }
  }

  function handleEvent(message: AgentEventStreamMessage): void {
    dispatch({ type: 'EVENT', event: message.data })
  }

  function startEvents(): void {
    const id = state.value.transportSessionId
    if (id === null || eventsAbortController !== null || state.value.connection === 'closed') {
      return
    }
    const controller = new AbortController()
    eventsAbortController = controller
    const run = ++eventsRun
    void client
      .streamEvents(id, state.value.cursor, { onEvent: handleEvent }, controller.signal)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          dispatch({ type: 'STREAM_ERROR', message: errorMessage(error) })
        }
      })
      .finally(() => {
        if (eventsRun === run) {
          eventsAbortController = null
        }
      })
  }

  function stopEvents(): void {
    eventsAbortController?.abort()
    eventsAbortController = null
  }

  async function connect(signal?: AbortSignal): Promise<SessionState> {
    if (state.value.connection === 'connecting') {
      throw new SessionCommandError('连接 Agent 中')
    }
    if (state.value.connection === 'connected' && state.value.transportSessionId !== null) {
      return state.value
    }
    dispatch({ type: 'CONNECTING' })
    try {
      const created = await client.createSession(signal)
      dispatch({
        type: 'CONNECTED',
        transportSessionId: created.transport_session_id,
        cursor: created.cursor,
        state: created.state,
      })
      savePersistedTransportSession(
        storage,
        { transportSessionId: created.transport_session_id, cursor: created.cursor },
        storageKey,
      )
      if (options.autoStartEvents !== false) {
        startEvents()
      }
      return state.value
    } catch (error) {
      dispatch({ type: 'STREAM_ERROR', message: errorMessage(error) })
      throw error
    }
  }

  function ensureSession(): string {
    const id = state.value.transportSessionId
    if (id === null || state.value.connection === 'closed') {
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
    clearPersistedTransportSession(storage, storageKey)
  }

  async function deleteSession(signal?: AbortSignal): Promise<void> {
    const id = ensureSession()
    await client.deleteSession(id, signal)
    stopEvents()
    dispatch({ type: 'CLOSED' })
    clearPersistedTransportSession(storage, storageKey)
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
    dispatch,
  }
}

export type { RuntimeState }
