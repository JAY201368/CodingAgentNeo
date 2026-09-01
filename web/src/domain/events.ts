import {
  AgentEventEnvelope,
  EventType,
  isNonNegativeInteger,
  isPublicEventType,
  isRecord,
  isRuntimeState,
  isPositiveSequence,
} from './protocol'

export type EventDiagnosticCode =
  | 'invalid_envelope'
  | 'unsupported_schema'
  | 'missing_field'
  | 'invalid_field'
  | 'unknown_event_type'
  | 'truncated_payload'
  | 'sequence_gap'

export interface EventDiagnostic {
  readonly code: EventDiagnosticCode
  readonly message: string
  readonly sequence?: number
  readonly expected?: number
}

export interface EventParseResult {
  readonly envelope: AgentEventEnvelope | null
  /** A safe sequence is exposed even when another field makes the envelope unusable. */
  readonly sequence: number | null
  readonly diagnostics: readonly EventDiagnostic[]
}

const FALLBACK_SESSION_ID = 'unknown-session'
const FALLBACK_AGENT_ID = 'unknown-agent'
const FALLBACK_TIMESTAMP = ''
const MAX_DISPLAY_TEXT_LENGTH = 4_000

function stringField(
  value: unknown,
  field: string,
  diagnostics: EventDiagnostic[],
  fallback: string,
  required = true,
): string {
  if (typeof value === 'string' && value.trim().length > 0) {
    return value
  }
  diagnostics.push({
    code: required ? 'missing_field' : 'invalid_field',
    message: required ? `${field} is missing` : `${field} is invalid`,
  })
  return fallback
}

function nullableStringField(
  value: unknown,
  field: string,
  diagnostics: EventDiagnostic[],
): string | null {
  if (value === null || value === undefined) {
    return null
  }
  if (typeof value === 'string' && value.trim().length > 0) {
    return value
  }
  diagnostics.push({ code: 'invalid_field', message: `${field} is invalid` })
  return null
}

function parseInput(input: unknown, diagnostics: EventDiagnostic[]): unknown {
  if (typeof input !== 'string') {
    return input
  }
  try {
    return JSON.parse(input) as unknown
  } catch {
    diagnostics.push({ code: 'invalid_envelope', message: 'event data is not valid JSON' })
    return null
  }
}

function fallbackPayload(
  diagnostics: EventDiagnostic[],
  reason: 'missing' | 'invalid',
): Readonly<Record<string, unknown>> {
  diagnostics.push({
    code: 'truncated_payload',
    message: reason === 'missing' ? 'event payload is missing' : 'event payload is invalid',
  })
  return Object.freeze({
    truncated: true,
    reason: reason === 'missing' ? 'payload_unavailable' : 'payload_not_an_object',
  })
}

function parseSequence(
  value: unknown,
  diagnostics: EventDiagnostic[],
): number | null {
  if (isPositiveSequence(value)) {
    return value
  }
  diagnostics.push({
    code: 'invalid_envelope',
    message: 'event sequence is invalid',
  })
  return null
}

/**
 * Parse one untrusted wire envelope without throwing.
 *
 * A v1 envelope with a valid sequence is retained with conservative fallback
 * values for missing metadata.  This lets the reducer advance the cursor only
 * after safely consuming a fact, while malformed schema/sequence values are
 * ignored and diagnosed.
 */
export function parseEventEnvelopeResult(input: unknown): EventParseResult {
  const diagnostics: EventDiagnostic[] = []
  const value = parseInput(input, diagnostics)
  if (!isRecord(value)) {
    diagnostics.push({ code: 'invalid_envelope', message: 'event envelope is not an object' })
    return { envelope: null, sequence: null, diagnostics }
  }

  if (value.schema_version !== 1) {
    diagnostics.push({
      code: 'unsupported_schema',
      message: 'event schema version is unsupported',
    })
    const sequence = parseSequence(value.sequence, diagnostics)
    return { envelope: null, sequence, diagnostics }
  }

  const sequence = parseSequence(value.sequence, diagnostics)
  if (sequence === null) {
    return { envelope: null, sequence: null, diagnostics }
  }

  const sessionId = stringField(value.session_id, 'session_id', diagnostics, FALLBACK_SESSION_ID)
  const eventId = stringField(
    value.event_id,
    'event_id',
    diagnostics,
    `unknown-event-${sequence}`,
  )
  const agentId = stringField(value.agent_id, 'agent_id', diagnostics, FALLBACK_AGENT_ID)
  const parentAgentId = nullableStringField(value.parent_agent_id, 'parent_agent_id', diagnostics)
  const timestamp = stringField(
    value.timestamp,
    'timestamp',
    diagnostics,
    FALLBACK_TIMESTAMP,
    false,
  )

  let type: EventType
  if (typeof value.type === 'string' && value.type.trim().length > 0) {
    type = value.type
    if (!isPublicEventType(value.type)) {
      diagnostics.push({ code: 'unknown_event_type', message: 'event type is unknown', sequence })
    }
  } else {
    diagnostics.push({ code: 'missing_field', message: 'type is missing', sequence })
    type = 'unknown'
  }

  let payload: Readonly<Record<string, unknown>>
  if (value.payload === undefined) {
    payload = fallbackPayload(diagnostics, 'missing')
  } else if (!isRecord(value.payload)) {
    payload = fallbackPayload(diagnostics, 'invalid')
  } else {
    payload = value.payload
    if (value.payload.truncated === true) {
      diagnostics.push({
        code: 'truncated_payload',
        message: 'event payload is a truncated preview',
        sequence,
      })
    }
  }

  const correlationId = nullableStringField(value.correlation_id, 'correlation_id', diagnostics)
  const providerToolCallId = nullableStringField(
    value.provider_tool_call_id,
    'provider_tool_call_id',
    diagnostics,
  )

  const envelope: AgentEventEnvelope = {
    schemaVersion: 1,
    sessionId,
    eventId,
    agentId,
    parentAgentId,
    sequence,
    type,
    correlationId,
    providerToolCallId,
    timestamp,
    payload,
  }

  return { envelope, sequence, diagnostics }
}

/** A compact parser facade for callers that only need a safe envelope/null. */
export function parseEventEnvelope(input: unknown): AgentEventEnvelope | null {
  return parseEventEnvelopeResult(input).envelope
}

/** Alias retained for callers that use the wire terminology. */
export const parseEnvelope = parseEventEnvelope

export function payloadString(
  payload: Readonly<Record<string, unknown>>,
  field: string,
): string | null {
  const value = payload[field]
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

export function payloadState(payload: Readonly<Record<string, unknown>>):
  | 'RUNNING'
  | 'WAITING_FOR_APPROVAL'
  | 'COMPLETED_TURN'
  | 'LIMIT_REACHED'
  | 'INTERRUPTED'
  | 'FAILED'
  | null {
  const value = payload.state
  return isRuntimeState(value) ? value : null
}

export function isTruncatedPayload(payload: Readonly<Record<string, unknown>>): boolean {
  return payload.truncated === true
}

/**
 * Convert untrusted event values to bounded plain text for interpolation.
 * Objects/arrays are deliberately not serialized into executable or rich HTML.
 */
export function safeDisplayText(value: unknown, limit = MAX_DISPLAY_TEXT_LENGTH): string {
  if (!Number.isSafeInteger(limit) || limit < 1) {
    return ''
  }
  let text: string
  if (typeof value === 'string') {
    text = value
  } else if (value === null || value === undefined) {
    text = ''
  } else if (typeof value === 'number' || typeof value === 'boolean') {
    text = String(value)
  } else {
    text = '[untrusted payload]'
  }
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 1))}…`
}

export function readSequence(input: unknown): number | null {
  if (!isRecord(input)) {
    return null
  }
  return isNonNegativeInteger(input.sequence) ? input.sequence : null
}
