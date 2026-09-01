/**
 * The browser-facing Agent HTTP/SSE wire types.
 *
 * This module intentionally contains transport DTOs only. It has no
 * knowledge of server ports, policy, tools, or execution semantics.
 */

export const PROTOCOL_VERSION = 1 as const
export const BASE_PATH = '/api/v1' as const

export const RUNTIME_STATES = [
  'RUNNING',
  'WAITING_FOR_APPROVAL',
  'COMPLETED_TURN',
  'LIMIT_REACHED',
  'INTERRUPTED',
  'FAILED',
] as const

export type RuntimeState = (typeof RUNTIME_STATES)[number]

export const PUBLIC_EVENT_TYPES = [
  'session_start',
  'agent_start',
  'user_message',
  'assistant_message',
  'tool_call',
  'approval_request',
  'policy_decision',
  'tool_result',
  'compaction',
  'retry',
  'turn_end',
  'error',
  'agent_end',
  'session_end',
] as const

export type PublicEventType = (typeof PUBLIC_EVENT_TYPES)[number]

/** An open union keeps newly added server event names forward compatible. */
export type EventType = PublicEventType | (string & {})

export interface HealthResponse {
  readonly status: 'ok'
  readonly protocol_version: typeof PROTOCOL_VERSION
}

export interface SessionCreatedResponse {
  readonly transport_session_id: string
  readonly state: RuntimeState
  readonly cursor: number
}

export interface SessionStatusResponse {
  readonly state: RuntimeState
  readonly cursor: number
  readonly closed: boolean
}

export interface AcceptedResponse {
  readonly accepted: true
}

export interface SubmitTaskCommand {
  readonly type: 'SubmitTask'
  readonly text: string
}

export interface ApprovalResponseCommand {
  readonly type: 'ApprovalResponse'
  readonly request_id: string
  readonly approved: boolean
}

export interface InterruptCommand {
  readonly type: 'Interrupt'
  readonly reason?: string
}

export interface CloseSessionCommand {
  readonly type: 'CloseSession'
  readonly reason?: string
}

export type AgentCommand =
  | SubmitTaskCommand
  | ApprovalResponseCommand
  | InterruptCommand
  | CloseSessionCommand

export type AgentCommandType = AgentCommand['type']

/** The canonical EventEnvelope v1 as seen by browser code. */
export interface AgentEventEnvelope {
  readonly schemaVersion: 1
  readonly sessionId: string
  readonly eventId: string
  readonly agentId: string
  readonly parentAgentId: string | null
  readonly sequence: number
  readonly type: EventType
  readonly correlationId: string | null
  readonly providerToolCallId: string | null
  readonly timestamp: string
  readonly payload: Readonly<Record<string, unknown>>
}

export type EventEnvelope = AgentEventEnvelope

export interface AgentEventStreamMessage {
  readonly id: string | null
  readonly event: string
  readonly data: unknown
  readonly rawData?: string
}

export interface PendingApproval {
  readonly requestId: string
  readonly correlationId: string
  readonly toolName: string
  readonly argumentsSummary: string
  readonly timeoutSeconds: number | null
}

export function isRuntimeState(value: unknown): value is RuntimeState {
  return typeof value === 'string' && (RUNTIME_STATES as readonly string[]).includes(value)
}

export function isPublicEventType(value: string): value is PublicEventType {
  return (PUBLIC_EVENT_TYPES as readonly string[]).includes(value)
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isNonNegativeInteger(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isSafeInteger(value) &&
    value >= 0
  )
}

export function isPositiveSequence(value: unknown): value is number {
  return isNonNegativeInteger(value) && value > 0
}
