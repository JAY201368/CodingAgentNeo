import {
  AgentCommandType,
  AgentEventEnvelope,
  PendingApproval,
  RuntimeState,
} from './protocol'
import {
  EventDiagnostic,
  payloadState,
  payloadString,
  parseEventEnvelopeResult,
} from './events'

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'closed' | 'error'

export type CommandGateKind =
  | 'connecting'
  | 'ready'
  | 'turn_running'
  | 'waiting_for_approval'
  | 'completed_turn'
  | 'terminal'
  | 'closed'
  | 'command_pending'

export interface SessionState {
  readonly transportSessionId: string | null
  readonly agentSessionId: string | null
  readonly connection: ConnectionState
  readonly status: RuntimeState
  readonly cursor: number
  readonly turnActive: boolean
  readonly pendingApproval: PendingApproval | null
  readonly commandInFlight: AgentCommandType | null
  readonly commandUncertain: boolean
  readonly lastEvent: AgentEventEnvelope | null
  /** Bounded facts retained for later projections; this is not a rendered timeline. */
  readonly events: readonly AgentEventEnvelope[]
  readonly latestAssistantText: string
  readonly finalAssistantText: string
  readonly diagnostics: readonly EventDiagnostic[]
  readonly needsResubscribe: boolean
  readonly lastError: string | null
}

export type SessionAction =
  | { readonly type: 'CONNECTING' }
  | {
      readonly type: 'CONNECTED'
      readonly transportSessionId: string
      readonly cursor: number
      readonly state: RuntimeState
    }
  | { readonly type: 'EVENT'; readonly event: unknown }
  | { readonly type: 'STREAM_ERROR'; readonly message: string }
  | { readonly type: 'STREAM_CLOSED' }
  | { readonly type: 'COMMAND_STARTED'; readonly commandType: AgentCommandType }
  | { readonly type: 'COMMAND_ACCEPTED'; readonly commandType: AgentCommandType }
  | {
      readonly type: 'COMMAND_FAILED'
      readonly commandType: AgentCommandType
      readonly message: string
      readonly uncertain?: boolean
    }
  | { readonly type: 'CLOSED' }
  | { readonly type: 'RESET' }

const MAX_RETAINED_EVENTS = 500
const MAX_DIAGNOSTICS = 100
const TERMINAL_STATES: readonly RuntimeState[] = ['LIMIT_REACHED', 'INTERRUPTED', 'FAILED']

export function createInitialSessionState(
  transportSessionId: string | null = null,
  cursor = 0,
): SessionState {
  const safeCursor = Number.isSafeInteger(cursor) && cursor >= 0 ? cursor : 0
  return {
    transportSessionId,
    agentSessionId: null,
    connection: 'disconnected',
    status: 'RUNNING',
    cursor: safeCursor,
    turnActive: false,
    pendingApproval: null,
    commandInFlight: null,
    commandUncertain: false,
    lastEvent: null,
    events: [],
    latestAssistantText: '',
    finalAssistantText: '',
    diagnostics: [],
    needsResubscribe: false,
    lastError: null,
  }
}

export function isTerminalState(state: RuntimeState): boolean {
  return TERMINAL_STATES.includes(state)
}

function diagnosticsWith(
  current: readonly EventDiagnostic[],
  additions: readonly EventDiagnostic[],
): readonly EventDiagnostic[] {
  if (additions.length === 0) {
    return current
  }
  return [...current, ...additions].slice(-MAX_DIAGNOSTICS)
}

function addDiagnostic(
  state: SessionState,
  diagnostic: EventDiagnostic,
): SessionState {
  return {
    ...state,
    diagnostics: diagnosticsWith(state.diagnostics, [diagnostic]),
  }
}

function withEvent(state: SessionState, event: AgentEventEnvelope): SessionState {
  return {
    ...state,
    agentSessionId: state.agentSessionId ?? event.sessionId,
    cursor: event.sequence,
    lastEvent: event,
    events: [...state.events, event].slice(-MAX_RETAINED_EVENTS),
  }
}

function eventStatus(
  state: SessionState,
  nextStatus: RuntimeState | null,
): SessionState {
  if (nextStatus === null || isTerminalState(state.status)) {
    return state
  }
  return { ...state, status: nextStatus }
}

function approvalFromEvent(event: AgentEventEnvelope): PendingApproval | null {
  const requestId = payloadString(event.payload, 'request_id')
  const correlationId = event.correlationId
  if (requestId === null || correlationId === null || requestId !== correlationId) {
    return null
  }
  const toolName = payloadString(event.payload, 'tool_name') ?? payloadString(event.payload, 'name') ?? ''
  const argumentsSummary = payloadString(event.payload, 'arguments_summary') ?? ''
  const timeoutValue = event.payload.timeout_seconds
  const timeoutSeconds = typeof timeoutValue === 'number' && Number.isFinite(timeoutValue)
    ? timeoutValue
    : null
  return { requestId, correlationId, toolName, argumentsSummary, timeoutSeconds }
}

function applyKnownEvent(
  state: SessionState,
  event: AgentEventEnvelope,
): SessionState {
  // Terminal facts are one-way. A late/replayed event must not reopen a
  // session; session_end remains consumable so the connection can close.
  if (isTerminalState(state.status) && event.type !== 'session_end') {
    return state
  }
  const eventState = payloadState(event.payload)
  switch (event.type) {
    case 'session_start':
    case 'agent_start':
      return eventStatus(state, eventState ?? 'RUNNING')
    case 'user_message':
      return isTerminalState(state.status)
        ? state
        : { ...state, status: 'RUNNING', turnActive: true }
    case 'assistant_message': {
      const text = payloadString(event.payload, 'text')
      return {
        ...eventStatus(state, 'RUNNING'),
        turnActive: isTerminalState(state.status) ? state.turnActive : true,
        latestAssistantText: text ?? state.latestAssistantText,
      }
    }
    case 'tool_call':
      return eventStatus(state, 'RUNNING')
    case 'approval_request': {
      const pending = approvalFromEvent(event)
      if (pending === null) {
        return addDiagnostic(state, {
          code: 'invalid_field',
          message: 'approval request correlation is invalid; approval remains closed',
          sequence: event.sequence,
        })
      }
      return { ...state, status: 'WAITING_FOR_APPROVAL', turnActive: true, pendingApproval: pending }
    }
    case 'policy_decision':
      if (state.pendingApproval !== null) {
        if (event.correlationId === state.pendingApproval.correlationId) {
          return { ...state, pendingApproval: null, status: 'RUNNING' }
        }
        return addDiagnostic(state, {
          code: 'invalid_field',
          message: 'policy decision correlation does not match the pending approval',
          sequence: event.sequence,
        })
      }
      return state
    case 'turn_end': {
      const nextStatus = eventState ?? state.status
      if (eventState === null) {
        // A turn boundary without a valid state is not enough evidence to
        // unlock follow-up input. Keep the command gate closed until a
        // subsequent canonical state fact arrives.
        return addDiagnostic(
          { ...state, turnActive: true, pendingApproval: null },
          {
            code: 'invalid_field',
            message: 'turn_end state is missing; follow-up remains locked',
            sequence: event.sequence,
          },
        )
      }
      const finalText = payloadString(event.payload, 'assistant_text') ?? state.latestAssistantText
      return {
        ...state,
        status: nextStatus,
        turnActive: false,
        pendingApproval: null,
        finalAssistantText: finalText,
      }
    }
    case 'agent_end':
      return eventState === null ? state : { ...state, status: eventState, turnActive: false }
    case 'session_end':
      return {
        ...state,
        connection: 'closed',
        status: eventState !== null && (!isTerminalState(state.status) || isTerminalState(eventState))
          ? eventState
          : state.status,
        turnActive: false,
        pendingApproval: null,
      }
    case 'error':
      // An error event is a process fact, not the turn boundary.  The status
      // remains governed by turn_end/session_end as required by the binding.
      return { ...state, lastError: payloadString(event.payload, 'message') ?? state.lastError }
    case 'retry':
    case 'compaction':
    case 'tool_result':
      return state
    default:
      return addDiagnostic(state, {
        code: 'unknown_event_type',
        message: 'unknown event retained without interpretation',
        sequence: event.sequence,
      })
  }
}

function reduceEvent(state: SessionState, input: unknown): SessionState {
  const parsed = parseEventEnvelopeResult(input)
  if (parsed.sequence !== null && parsed.sequence <= state.cursor) {
    // Duplicate and replayed sequences are intentionally idempotent.  Do not
    // append a diagnostic, since a replay should be a no-op.
    return state
  }
  if (parsed.envelope === null) {
    return {
      ...state,
      diagnostics: diagnosticsWith(state.diagnostics, parsed.diagnostics),
    }
  }
  const event = parsed.envelope
  if (event.sequence > state.cursor + 1) {
    return {
      ...state,
      needsResubscribe: true,
      diagnostics: diagnosticsWith(state.diagnostics, [
        ...parsed.diagnostics,
        {
          code: 'sequence_gap',
          message: 'event sequence has a gap; resubscribe from the last cursor',
          sequence: event.sequence,
          expected: state.cursor + 1,
        },
      ]),
    }
  }
  const consumed = withEvent(state, event)
  const diagnosed = {
    ...consumed,
    diagnostics: diagnosticsWith(consumed.diagnostics, parsed.diagnostics),
  }
  return applyKnownEvent(diagnosed, event)
}

export function reduceSession(state: SessionState, action: SessionAction): SessionState {
  switch (action.type) {
    case 'CONNECTING':
      return {
        ...state,
        connection: 'connecting',
        lastError: null,
        commandInFlight: null,
        commandUncertain: false,
      }
    case 'CONNECTED':
      return {
        ...state,
        transportSessionId: action.transportSessionId,
        connection: 'connected',
        status: action.state,
        cursor: action.cursor,
        turnActive: false,
        pendingApproval: null,
        commandInFlight: null,
        commandUncertain: false,
        lastError: null,
      }
    case 'EVENT':
      return reduceEvent(state, action.event)
    case 'STREAM_ERROR':
      return {
        ...state,
        connection: state.transportSessionId === null || state.connection === 'connecting'
          ? 'error'
          : 'connected',
        lastError: action.message,
      }
    case 'STREAM_CLOSED':
      return state.connection === 'closed'
        ? state
        : { ...state, connection: state.transportSessionId === null ? 'error' : 'connected' }
    case 'COMMAND_STARTED':
      return {
        ...state,
        commandInFlight: action.commandType,
        commandUncertain: false,
        ...(action.commandType === 'SubmitTask' && !isTerminalState(state.status)
          ? { status: 'RUNNING' as const, turnActive: true }
          : {}),
      }
    case 'COMMAND_ACCEPTED':
      return {
        ...state,
        commandInFlight: null,
        commandUncertain: false,
        ...(action.commandType === 'CloseSession' ? { connection: 'closed' as const } : {}),
      }
    case 'COMMAND_FAILED':
      return {
        ...state,
        commandInFlight: action.uncertain === true ? action.commandType : null,
        commandUncertain: action.uncertain === true,
        turnActive: action.uncertain === true && action.commandType === 'SubmitTask'
          ? true
          : action.uncertain === true
            ? state.turnActive
            : action.commandType === 'SubmitTask'
              ? false
              : state.turnActive,
        lastError: action.message,
      }
    case 'CLOSED':
      return {
        ...state,
        connection: 'closed',
        commandInFlight: null,
        commandUncertain: false,
        turnActive: false,
        pendingApproval: null,
      }
    case 'RESET':
      return createInitialSessionState()
  }
}

export interface CommandGate {
  readonly kind: CommandGateKind
  readonly canSubmitTask: boolean
  readonly canRespondToApproval: boolean
  readonly canInterrupt: boolean
  readonly canClose: boolean
  readonly reason: string
}

export function commandGateFor(state: SessionState): CommandGate {
  if (state.connection === 'connecting') {
    return {
      kind: 'connecting',
      canSubmitTask: false,
      canRespondToApproval: false,
      canInterrupt: false,
      canClose: false,
      reason: '连接 Agent 中',
    }
  }
  if (state.connection !== 'connected' || state.transportSessionId === null) {
    return {
      kind: 'closed',
      canSubmitTask: false,
      canRespondToApproval: false,
      canInterrupt: false,
      canClose: false,
      reason: state.connection === 'error' ? 'Agent 连接不可用' : 'session 尚未连接或已关闭',
    }
  }
  if (state.status === 'WAITING_FOR_APPROVAL') {
    return {
      kind: 'waiting_for_approval',
      canSubmitTask: false,
      canRespondToApproval: state.pendingApproval !== null && state.commandInFlight === null,
      canInterrupt: state.turnActive && state.commandInFlight === null,
      canClose: true,
      reason: state.commandInFlight === null ? '等待唯一授权响应' : '授权命令仍在提交',
    }
  }
  if (state.turnActive) {
    return {
      kind: 'turn_running',
      canSubmitTask: false,
      canRespondToApproval: false,
      canInterrupt: state.commandInFlight === null,
      canClose: true,
      reason: state.commandInFlight === null ? 'turn 正在运行' : 'turn 命令仍在提交',
    }
  }
  if (isTerminalState(state.status)) {
    return {
      kind: 'terminal',
      canSubmitTask: false,
      canRespondToApproval: false,
      canInterrupt: false,
      canClose: true,
      reason: 'session 已进入终止状态',
    }
  }
  if (state.commandUncertain || state.commandInFlight !== null) {
    return {
      kind: 'command_pending',
      canSubmitTask: false,
      canRespondToApproval: false,
      canInterrupt: false,
      canClose: true,
      reason: '上一条命令结果未知或仍在提交',
    }
  }
  if (state.status === 'COMPLETED_TURN') {
    return {
      kind: 'completed_turn',
      canSubmitTask: true,
      canRespondToApproval: false,
      canInterrupt: false,
      canClose: true,
      reason: '上一 turn 已完成，可继续 follow-up',
    }
  }
  return {
    kind: 'ready',
    canSubmitTask: true,
    canRespondToApproval: false,
    canInterrupt: false,
    canClose: true,
    reason: '可提交任务',
  }
}

export const sessionReducer = reduceSession
export const reduceEventEnvelope = reduceEvent

export type { AgentEventEnvelope, RuntimeState }
