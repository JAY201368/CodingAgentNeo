import type { AgentEventEnvelope } from './protocol'
import { isTruncatedPayload, payloadString, safeDisplayText } from './events'
import { isRecord } from './protocol'

/** Public tool-result statuses from the HTTP/SSE binding. */
export type ToolResultStatus =
  | 'success'
  | 'error'
  | 'denied'
  | 'invalid'
  | 'cancelled'
  | 'timeout'

export interface ToolLifecycle {
  /** The canonical envelope correlation ID; provider IDs are never used here. */
  readonly correlationId: string
  readonly providerToolCallId: string | null
  readonly toolName: string
  readonly firstSequence: number
  readonly toolCall: AgentEventEnvelope | null
  readonly approvalRequest: AgentEventEnvelope | null
  readonly policyDecision: AgentEventEnvelope | null
  readonly toolResult: AgentEventEnvelope | null
  readonly approvalRequestId: string | null
  readonly approvalCorrelationValid: boolean
  readonly approvalSummary: string | null
  readonly policyDecisionText: string | null
  readonly resultReceived: boolean
  readonly resultStatus: ToolResultStatus | null
  readonly resultText: string | null
  readonly durationSeconds: number | null
  readonly exitCode: number | null
  readonly timedOut: boolean
  readonly originalLength: number | null
  readonly truncated: boolean
}

/** Compatibility alias for callers that use the display-model terminology. */
export type ToolLifecycleItem = ToolLifecycle

interface MutableToolLifecycle {
  correlationId: string
  providerToolCallId: string | null
  toolName: string
  firstSequence: number
  toolCall: AgentEventEnvelope | null
  approvalRequest: AgentEventEnvelope | null
  policyDecision: AgentEventEnvelope | null
  toolResult: AgentEventEnvelope | null
  approvalRequestId: string | null
  approvalCorrelationValid: boolean
  approvalSummary: string | null
  policyDecisionText: string | null
  resultReceived: boolean
  resultStatus: ToolResultStatus | null
  resultText: string | null
  durationSeconds: number | null
  exitCode: number | null
  timedOut: boolean
  originalLength: number | null
  truncated: boolean
}

const RESULT_STATUSES: readonly ToolResultStatus[] = [
  'success',
  'error',
  'denied',
  'invalid',
  'cancelled',
  'timeout',
]

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

function toolNameFrom(event: AgentEventEnvelope): string | null {
  return nonEmptyString(event.payload.tool_name) ?? nonEmptyString(event.payload.name)
}

function resultPayload(event: AgentEventEnvelope): Readonly<Record<string, unknown>> {
  if (isRecord(event.payload.result)) {
    return { ...event.payload, ...event.payload.result }
  }
  if (isRecord(event.payload.tool_result)) {
    return { ...event.payload, ...event.payload.tool_result }
  }
  return event.payload
}

function resultTextFrom(event: AgentEventEnvelope): string | null {
  const result = resultPayload(event)
  const direct = nonEmptyString(result.text)
  if (direct !== null) {
    return safeDisplayText(direct, 20_000)
  }
  // `result` and `tool_result` are allowed compatibility aliases, but their
  // object values are intentionally never serialized into browser text.
  const resultValue = event.payload.result
  if (typeof resultValue === 'string' && resultValue.trim().length > 0) {
    return safeDisplayText(resultValue, 20_000)
  }
  const alias = event.payload.tool_result
  return typeof alias === 'string' && alias.trim().length > 0
    ? safeDisplayText(alias, 20_000)
    : null
}

function finiteNonNegative(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

function exitCodeFrom(event: AgentEventEnvelope): number | null {
  const result = resultPayload(event)
  return typeof result.exit_code === 'number' && Number.isSafeInteger(result.exit_code)
    ? result.exit_code
    : null
}

function resultStatusFrom(event: AgentEventEnvelope): ToolResultStatus | null {
  const status = resultPayload(event).status
  return typeof status === 'string' && (RESULT_STATUSES as readonly string[]).includes(status)
    ? status as ToolResultStatus
    : null
}

function createLifecycle(event: AgentEventEnvelope): MutableToolLifecycle {
  return {
    correlationId: event.correlationId ?? '',
    providerToolCallId: event.providerToolCallId,
    toolName: toolNameFrom(event) ?? '未知工具',
    firstSequence: event.sequence,
    toolCall: null,
    approvalRequest: null,
    policyDecision: null,
    toolResult: null,
    approvalRequestId: null,
    approvalCorrelationValid: false,
    approvalSummary: null,
    policyDecisionText: null,
    resultReceived: false,
    resultStatus: null,
    resultText: null,
    durationSeconds: null,
    exitCode: null,
    timedOut: false,
    originalLength: null,
    truncated: false,
  }
}

function applyEvent(lifecycle: MutableToolLifecycle, event: AgentEventEnvelope): void {
  lifecycle.providerToolCallId ??= event.providerToolCallId
  lifecycle.toolName = toolNameFrom(event) ?? lifecycle.toolName
  lifecycle.firstSequence = Math.min(lifecycle.firstSequence, event.sequence)

  switch (event.type) {
    case 'tool_call':
      lifecycle.toolCall ??= event
      break
    case 'approval_request': {
      lifecycle.approvalRequest ??= event
      const requestId = nonEmptyString(event.payload.request_id)
      lifecycle.approvalRequestId = requestId
      lifecycle.approvalCorrelationValid =
        requestId !== null && event.correlationId !== null && requestId === event.correlationId
      lifecycle.approvalSummary = nonEmptyString(event.payload.arguments_summary)
        ? safeDisplayText(event.payload.arguments_summary, 20_000)
        : null
      break
    }
    case 'policy_decision':
      lifecycle.policyDecision ??= event
      lifecycle.policyDecisionText = safeDisplayText(
        payloadString(event.payload, 'decision') ??
          payloadString(event.payload, 'action') ??
          payloadString(event.payload, 'reason') ??
          '策略决定不可用',
        2_000,
      )
      break
    case 'tool_result': {
      lifecycle.toolResult ??= event
      lifecycle.resultReceived = true
      lifecycle.resultStatus = resultStatusFrom(event)
      lifecycle.resultText = resultTextFrom(event)
      const result = resultPayload(event)
      lifecycle.durationSeconds = finiteNonNegative(result.duration_seconds)
      lifecycle.exitCode = exitCodeFrom(event)
      lifecycle.timedOut = result.timed_out === true
      lifecycle.originalLength = typeof result.original_length === 'number' &&
          Number.isSafeInteger(result.original_length) && result.original_length >= 0
        ? result.original_length
        : null
      lifecycle.truncated = isTruncatedPayload(event.payload) || result.truncated === true
      break
    }
    default:
      break
  }
}

function freezeLifecycle(lifecycle: MutableToolLifecycle): ToolLifecycle {
  return Object.freeze({ ...lifecycle })
}

/**
 * Aggregate canonical tool lifecycle facts by envelope correlation ID.
 *
 * Only the envelope correlation ID is used as a grouping key.  Payload values
 * (including arguments and provider IDs) remain display-only and never become
 * commands or executable data.
 */
export function projectToolLifecycles(
  events: readonly AgentEventEnvelope[],
): readonly ToolLifecycle[] {
  const groups = new Map<string, MutableToolLifecycle>()
  for (const event of events) {
    if (typeof event.correlationId !== 'string' || event.correlationId.trim().length === 0) {
      continue
    }
    let lifecycle = groups.get(event.correlationId)
    if (lifecycle === undefined) {
      lifecycle = createLifecycle(event)
      groups.set(event.correlationId, lifecycle)
    }
    applyEvent(lifecycle, event)
  }
  return [...groups.values()]
    .sort((left, right) => left.firstSequence - right.firstSequence)
    .map(freezeLifecycle)
}

/** Alias retained for display callers that prefer a shorter projection name. */
export const projectTools = projectToolLifecycles
