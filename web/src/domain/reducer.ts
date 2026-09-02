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
  /** A reattached RUNNING snapshot is ambiguous until a canonical boundary. */
  readonly resumeStateAmbiguous: boolean
  readonly pendingApproval: PendingApproval | null
  readonly commandInFlight: AgentCommandType | null
  readonly commandUncertain: boolean
  /** Whether the browser still has a usable event stream attachment. */
  readonly streamAvailable: boolean
  readonly lastEvent: AgentEventEnvelope | null
  /** Bounded facts retained for later projections; this is not a rendered timeline. */
  readonly events: readonly AgentEventEnvelope[]
  readonly latestAssistantText: string
  readonly finalAssistantText: string
  readonly diagnostics: readonly EventDiagnostic[]
  readonly needsResubscribe: boolean
  /** Automatic GET/SSE retry reached its finite budget. */
  readonly streamRetryExhausted?: boolean
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
  | {
      readonly type: 'SESSION_ATTACHED'
      readonly transportSessionId: string
      readonly cursor: number
      readonly state: RuntimeState
    }
  | { readonly type: 'EVENT'; readonly event: unknown }
  | { readonly type: 'HYDRATE_EVENT'; readonly event: unknown }
  | { readonly type: 'STREAM_OPENED' }
  | { readonly type: 'STREAM_ERROR'; readonly message: string }
  | { readonly type: 'STREAM_CLOSED' }
  | { readonly type: 'STREAM_RETRY_EXHAUSTED'; readonly message: string }
  | { readonly type: 'COMMAND_STARTED'; readonly commandType: AgentCommandType }
  | { readonly type: 'COMMAND_ACCEPTED'; readonly commandType: AgentCommandType }
  | {
      readonly type: 'COMMAND_FAILED'
      readonly commandType: AgentCommandType
      readonly message: string
      readonly uncertain?: boolean
    }
  | { readonly type: 'CLOSED' }
  | { readonly type: 'SESSION_UNAVAILABLE'; readonly message: string }
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
    resumeStateAmbiguous: false,
    pendingApproval: null,
    commandInFlight: null,
    commandUncertain: false,
    streamAvailable: false,
    lastEvent: null,
    events: [],
    latestAssistantText: '',
    finalAssistantText: '',
    diagnostics: [],
    needsResubscribe: false,
    streamRetryExhausted: false,
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
        // A malformed approval request is still evidence that a turn may be
        // waiting on a tool. Keep input closed and expose Stop, but never
        // create a pending approval or infer an ID from payload text.
        const invalidState = state.pendingApproval === null && !isTerminalState(state.status)
          ? { ...state, status: 'RUNNING' as const, turnActive: true }
          : state
        return addDiagnostic(invalidState, {
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
          return {
            ...state,
            pendingApproval: null,
            // A matching policy fact is the acknowledgement boundary for an
            // accepted approval response.  Interrupt remains locked until its
            // own turn_end boundary.
            commandInFlight: state.commandInFlight === 'ApprovalResponse'
              ? null
              : state.commandInFlight,
            status: 'RUNNING',
          }
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
        resumeStateAmbiguous: false,
        pendingApproval: null,
        commandInFlight: null,
        commandUncertain: false,
        finalAssistantText: finalText,
      }
    }
    case 'agent_end':
      return eventState === null
        ? state
        : {
            ...state,
            status: eventState,
            // A reattached RUNNING snapshot is not unlocked by agent_end
            // alone. The binding makes turn_end the only turn boundary;
            // session_end is the lifecycle boundary that can close the
            // remaining ambiguity when a history prefix is missing.
            turnActive: state.resumeStateAmbiguous ? true : false,
            pendingApproval: null,
            commandInFlight: null,
            commandUncertain: false,
          }
    case 'session_end':
      return {
        ...state,
        connection: 'closed',
        status: eventState !== null && (!isTerminalState(state.status) || isTerminalState(eventState))
          ? eventState
          : state.status,
        turnActive: false,
        resumeStateAmbiguous: false,
        pendingApproval: null,
        commandInFlight: null,
        commandUncertain: false,
        streamAvailable: false,
        streamRetryExhausted: false,
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
    streamAvailable: true,
    diagnostics: diagnosticsWith(consumed.diagnostics, parsed.diagnostics),
  }
  return applyKnownEvent(diagnosed, event)
}

/**
 * Finite history hydration reuses the live parser, timeline facts, sequence
 * cursor, and diagnostics. Historical agent_end / session_end / INTERRUPTED
 * must not close the live connection, drop the POST-owned transport ID, or
 * replace the live runtime state with a terminal snapshot.
 */
function reduceHydrateEvent(state: SessionState, input: unknown): SessionState {
  const liveConnection = state.connection
  const liveTransportSessionId = state.transportSessionId
  const liveStatus = state.status
  const liveStreamAvailable = state.streamAvailable
  const liveStreamRetryExhausted = state.streamRetryExhausted
  const reduced = reduceEvent(state, input)
  return {
    ...reduced,
    connection: liveConnection,
    transportSessionId: liveTransportSessionId,
    streamAvailable: liveStreamAvailable,
    streamRetryExhausted: liveStreamRetryExhausted,
    status: isTerminalState(reduced.status) && !isTerminalState(liveStatus)
      ? liveStatus
      : reduced.status,
  }
}

export function reduceSession(state: SessionState, action: SessionAction): SessionState {
  switch (action.type) {
    case 'CONNECTING':
      return {
        ...state,
        connection: 'connecting',
        lastError: null,
        streamAvailable: false,
        streamRetryExhausted: false,
      }
    case 'CONNECTED':
      return {
        ...state,
        transportSessionId: action.transportSessionId,
        connection: 'connected',
        status: action.state,
        cursor: action.cursor,
        turnActive: false,
        resumeStateAmbiguous: false,
        pendingApproval: null,
        commandInFlight: null,
        commandUncertain: false,
        streamAvailable: false,
        streamRetryExhausted: false,
        lastError: null,
      }
    case 'SESSION_ATTACHED':
      return {
        ...state,
        transportSessionId: action.transportSessionId,
        connection: 'connected',
        // The cursor belongs to the browser's last successfully consumed
        // event.  A server status cursor is only a diagnostic and must never
        // make the browser skip canonical events during re-attach.
        cursor: Math.max(state.cursor, action.cursor),
        status: action.state,
        // A WAITING snapshot is sufficient evidence that a turn is active,
        // even when the approval event predates this browser attachment. A
        // RUNNING snapshot is ambiguous (it may be the initial no-turn state
        // or an active turn), so fail closed until a canonical turn_end or
        // session_end is consumed. Never infer activity from the server
        // cursor.
        turnActive: action.state === 'RUNNING' || action.state === 'WAITING_FOR_APPROVAL'
          ? true
          : state.turnActive,
        resumeStateAmbiguous: action.state === 'RUNNING',
        diagnostics: action.state === 'RUNNING'
          ? diagnosticsWith(state.diagnostics, [{
              code: 'invalid_field',
              message: 'reattached RUNNING snapshot is ambiguous; follow-up remains locked until a canonical turn_end or session_end',
            }])
          : state.diagnostics,
        streamAvailable: false,
        needsResubscribe: false,
        streamRetryExhausted: false,
        lastError: null,
      }
    case 'EVENT':
      return reduceEvent(state, action.event)
    case 'HYDRATE_EVENT':
      return reduceHydrateEvent(state, action.event)
    case 'STREAM_OPENED':
      return state.connection === 'closed'
        ? state
        : {
            ...state,
            streamAvailable: true,
            needsResubscribe: false,
            streamRetryExhausted: false,
            lastError: null,
          }
    case 'STREAM_ERROR':
      return {
        ...state,
        connection: state.connection === 'closed'
          ? 'closed'
          : state.transportSessionId === null || state.connection === 'connecting'
          ? 'error'
          : 'connected',
        lastError: action.message,
        streamAvailable: false,
        streamRetryExhausted: false,
      }
    case 'STREAM_CLOSED':
      return state.connection === 'closed'
        ? state
        : {
            ...state,
            connection: state.transportSessionId === null ? 'error' : 'connected',
            streamAvailable: false,
            streamRetryExhausted: false,
          }
    case 'STREAM_RETRY_EXHAUSTED':
      return state.connection === 'closed'
        ? state
        : {
            ...state,
            // Exhausting the finite attachment budget means the browser can
            // no longer safely issue commands against this transport.  Keep
            // the opaque resume hint in the composable so a manual retry can
            // query the same session before deciding whether a new one is
            // needed.
            connection: 'error',
            streamAvailable: false,
            streamRetryExhausted: true,
            lastError: action.message,
          }
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
        // A 202 only acknowledges receipt.  Approval remains locked until a
        // matching policy_decision, and Stop remains locked until turn_end.
        commandInFlight: (
          (action.commandType === 'ApprovalResponse' && state.pendingApproval !== null) ||
          (action.commandType === 'Interrupt' && state.turnActive)
        ) ? action.commandType : null,
        commandUncertain: false,
        ...(action.commandType === 'CloseSession'
          ? { connection: 'closed' as const, streamAvailable: false }
          : {}),
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
        streamAvailable: false,
        streamRetryExhausted: false,
        turnActive: false,
        resumeStateAmbiguous: false,
        pendingApproval: null,
      }
    case 'SESSION_UNAVAILABLE': {
      const reset = createInitialSessionState()
      return {
        ...reset,
        connection: 'error',
        lastError: action.message,
      }
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
    const approvalResponsePending = state.commandInFlight === 'ApprovalResponse'
    return {
      kind: 'waiting_for_approval',
      canSubmitTask: false,
      canRespondToApproval: state.pendingApproval !== null &&
        !approvalResponsePending &&
        state.streamAvailable,
      canInterrupt: state.turnActive && state.commandInFlight === null,
      canClose: true,
      reason: approvalResponsePending
        ? '授权已提交，等待策略事件确认'
        : state.pendingApproval === null
          ? '授权请求无效，未发送授权；可使用 Stop 中断'
          : !state.streamAvailable
            ? '事件流已断开，授权保持关闭；可使用 Stop 中断'
            : '等待唯一授权响应',
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
  if (state.resumeStateAmbiguous) {
    return {
      kind: 'turn_running',
      canSubmitTask: false,
      canRespondToApproval: false,
      canInterrupt: state.commandInFlight === null,
      canClose: true,
      reason: '重新连接得到的 RUNNING 状态可能仍有未完成 turn；在收到 turn_end 或 session_end 前不能提交新任务。需要时可结束 Session。',
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
