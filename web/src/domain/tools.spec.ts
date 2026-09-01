import { describe, expect, it } from 'vitest'

import { parseEventEnvelope } from './events'
import { projectToolLifecycles } from './tools'

function event(
  sequence: number,
  type: string,
  correlationId: string | null,
  payload: Record<string, unknown>,
  providerToolCallId = 'provider-tool-1',
) {
  const parsed = parseEventEnvelope({
    schema_version: 1,
    session_id: 'session-tools-test',
    event_id: `event-tools-test-${sequence}`,
    agent_id: 'agent-tools-test',
    parent_agent_id: null,
    sequence,
    type,
    correlation_id: correlationId,
    provider_tool_call_id: providerToolCallId,
    timestamp: '2026-09-01T00:00:00.000000Z',
    payload,
  })
  if (parsed === null) {
    throw new Error('test event is invalid')
  }
  return parsed
}

describe('tool lifecycle projection', () => {
  it('groups tool, approval, policy, and result facts by canonical correlation ID', () => {
    const items = projectToolLifecycles([
      event(4, 'tool_result', 'correlation-two', {
        status: 'error',
        text: 'safe failure',
        duration_seconds: 1.25,
        exit_code: 7,
      }, 'provider-two'),
      event(1, 'tool_call', 'correlation-one', {
        tool_name: 'read_file',
        arguments: '{"path":"redacted"}',
      }),
      event(3, 'policy_decision', 'correlation-one', {
        decision: 'allow',
        reason: 'approved by user',
      }),
      event(2, 'approval_request', 'correlation-one', {
        request_id: 'correlation-one',
        tool_name: 'read_file',
        arguments_summary: 'redacted summary',
        timeout_seconds: 20,
      }),
      event(5, 'tool_result', 'correlation-one', {
        result: {
          status: 'success',
          text: 'safe result',
          duration_seconds: 0.5,
          exit_code: 0,
        },
      }),
    ])

    expect(items).toHaveLength(2)
    expect(items[0]).toMatchObject({
      correlationId: 'correlation-one',
      providerToolCallId: 'provider-tool-1',
      toolName: 'read_file',
      approvalSummary: 'redacted summary',
      policyDecisionText: 'allow',
      resultStatus: 'success',
      resultReceived: true,
      resultText: 'safe result',
      durationSeconds: 0.5,
      exitCode: 0,
    })
    expect(items[1]).toMatchObject({
      correlationId: 'correlation-two',
      providerToolCallId: 'provider-two',
      resultStatus: 'error',
      durationSeconds: 1.25,
      exitCode: 7,
    })
    expect(items[0].toolCall?.payload.arguments).toBe('{"path":"redacted"}')
  })

  it('keeps malformed approval fail-closed and never stringifies object payloads', () => {
    const [item] = projectToolLifecycles([
      event(1, 'approval_request', 'correlation-one', {
        request_id: 'other-id',
        arguments_summary: { secret: 'do not execute' },
      }),
      event(2, 'tool_result', 'correlation-one', {
        status: 'timeout',
        result: { secret: 'do not execute' },
        truncated: true,
      }),
    ])

    expect(item.approvalCorrelationValid).toBe(false)
    expect(item.approvalRequestId).toBe('other-id')
    expect(item.approvalSummary).toBeNull()
    expect(item.resultText).toBeNull()
    expect(item.resultStatus).toBe('timeout')
    expect(item.truncated).toBe(true)
  })
})
