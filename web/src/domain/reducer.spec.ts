import fixture from './fixtures/transport-v1.json'
import { describe, expect, it } from 'vitest'

import { parseEventEnvelopeResult, safeDisplayText } from './events'
import {
  commandGateFor,
  createInitialSessionState,
  reduceSession,
} from './reducer'

function eventAt(index: number): unknown {
  return fixture.events[index]
}

describe('defensive event parser and reducer', () => {
  it('preserves canonical metadata and unknown fields while recognizing truncation', () => {
    const parsed = parseEventEnvelopeResult(eventAt(1))
    expect(parsed.envelope).toMatchObject({
      schemaVersion: 1,
      sessionId: 'session_fixture_1',
      sequence: 2,
      type: 'assistant_message',
      payload: { text: 'done', new_field: { preserve: true } },
    })
    expect(parseEventEnvelopeResult({ ...fixture.events[1], payload: { truncated: true } }).diagnostics)
      .toEqual(expect.arrayContaining([expect.objectContaining({ code: 'truncated_payload' })]))
    const missingPayload = parseEventEnvelopeResult({ ...fixture.events[1], payload: undefined })
    expect(missingPayload.envelope?.payload).toMatchObject({ truncated: true })
    expect(missingPayload.envelope?.payload.text).toBeUndefined()
  })

  it('ignores invalid schema safely and never advances without a valid sequence', () => {
    const state = createInitialSessionState('transport_fixture_1', 0)
    const invalid = reduceSession(state, {
      type: 'EVENT',
      event: { schema_version: 1, type: 'assistant_message', payload: {} },
    })
    expect(invalid.cursor).toBe(0)
    expect(invalid.events).toHaveLength(0)
    expect(invalid.diagnostics.length).toBeGreaterThan(0)
  })

  it('advances on ordered facts, makes replay idempotent, and diagnoses gaps', () => {
    let state = createInitialSessionState('transport_fixture_1')
    state = reduceSession(state, { type: 'EVENT', event: eventAt(0) })
    state = reduceSession(state, { type: 'EVENT', event: eventAt(1) })
    expect(state.cursor).toBe(2)
    expect(state.latestAssistantText).toBe('done')
    const duplicate = reduceSession(state, { type: 'EVENT', event: eventAt(1) })
    expect(duplicate).toBe(state)

    const gap = reduceSession(state, {
      type: 'EVENT',
      event: { ...fixture.events[3], sequence: 4 },
    })
    expect(gap.cursor).toBe(2)
    expect(gap.events).toHaveLength(2)
    expect(gap.needsResubscribe).toBe(true)
    expect(gap.diagnostics).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'sequence_gap', expected: 3, sequence: 4 }),
    ]))
  })

  it('keeps unknown event types safe and uses turn_end as the completion boundary', () => {
    let state = createInitialSessionState()
    state = reduceSession(state, {
      type: 'CONNECTED',
      transportSessionId: 'transport_fixture_1',
      cursor: 0,
      state: 'RUNNING',
    })
    for (const event of fixture.events) {
      state = reduceSession(state, { type: 'EVENT', event })
    }
    expect(state.cursor).toBe(4)
    expect(state.status).toBe('COMPLETED_TURN')
    expect(state.finalAssistantText).toBe('done')
    expect(state.diagnostics).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'unknown_event_type' }),
    ]))
    expect(commandGateFor(state)).toMatchObject({ kind: 'completed_turn', canSubmitTask: true })
  })

  it('requires approval correlation and distinguishes all command mutex states', () => {
    let state = createInitialSessionState()
    state = reduceSession(state, {
      type: 'CONNECTED',
      transportSessionId: 'transport_fixture_1',
      cursor: 0,
      state: 'RUNNING',
    })
    expect(commandGateFor(state).kind).toBe('ready')
    state = reduceSession(state, { type: 'COMMAND_STARTED', commandType: 'SubmitTask' })
    expect(commandGateFor(state).kind).toBe('turn_running')
    state = reduceSession(state, { type: 'COMMAND_ACCEPTED', commandType: 'SubmitTask' })
    state = reduceSession(state, { type: 'EVENT', event: {
      ...fixture.events[0],
      sequence: 1,
      type: 'approval_request',
      correlation_id: 'correlation_fixture_1',
      payload: {
        request_id: 'correlation_fixture_1',
        tool_name: 'read_file',
        arguments_summary: 'safe summary',
        timeout_seconds: 10,
      },
    } })
    expect(commandGateFor(state).kind).toBe('waiting_for_approval')
    expect(commandGateFor(state).canRespondToApproval).toBe(true)

    const mismatch = reduceSession(state, { type: 'EVENT', event: {
      ...fixture.events[1],
      sequence: 2,
      type: 'approval_request',
      correlation_id: 'other',
      payload: { request_id: 'different' },
    } })
    expect(mismatch.pendingApproval?.requestId).toBe('correlation_fixture_1')

    const error = reduceSession(mismatch, { type: 'EVENT', event: {
      ...fixture.events[2],
      sequence: 3,
      type: 'error',
      payload: { state: 'FAILED', message: 'safe' },
    } })
    expect(error.status).toBe('WAITING_FOR_APPROVAL')
    const terminal = reduceSession(error, { type: 'EVENT', event: {
      ...fixture.events[3],
      sequence: 4,
      type: 'turn_end',
      payload: { state: 'INTERRUPTED', assistant_text: '' },
    } })
    expect(commandGateFor(terminal).kind).toBe('terminal')
    expect(commandGateFor(terminal).canSubmitTask).toBe(false)
  })

  it('projects untrusted values as bounded plain text only', () => {
    expect(safeDisplayText({ secret: 'do not execute' })).toBe('[untrusted payload]')
    expect(safeDisplayText('123456', 4)).toBe('123…')
  })

  it('fails closed when a turn boundary omits its required state', () => {
    let state = createInitialSessionState()
    state = reduceSession(state, {
      type: 'CONNECTED',
      transportSessionId: 'transport_fixture_1',
      cursor: 0,
      state: 'RUNNING',
    })
    state = reduceSession(state, { type: 'EVENT', event: { ...fixture.events[0], sequence: 1 } })
    state = reduceSession(state, { type: 'EVENT', event: {
      ...fixture.events[1],
      sequence: 2,
      type: 'turn_end',
      payload: { assistant_text: 'maybe' },
    } })
    expect(state.cursor).toBe(2)
    expect(state.turnActive).toBe(true)
    expect(commandGateFor(state).kind).toBe('turn_running')
    expect(state.diagnostics).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'invalid_field' }),
    ]))
  })

  it('keeps an accepted approval locked until the matching policy fact arrives', () => {
    let state = createInitialSessionState()
    state = reduceSession(state, {
      type: 'CONNECTED',
      transportSessionId: 'transport_fixture_1',
      cursor: 0,
      state: 'RUNNING',
    })
    state = reduceSession(state, { type: 'EVENT', event: {
      ...fixture.events[0],
      sequence: 1,
      type: 'approval_request',
      correlation_id: 'correlation_fixture_1',
      payload: {
        request_id: 'correlation_fixture_1',
        tool_name: 'read_file',
        arguments_summary: 'safe summary',
      },
    } })
    state = reduceSession(state, { type: 'COMMAND_STARTED', commandType: 'ApprovalResponse' })
    state = reduceSession(state, { type: 'COMMAND_ACCEPTED', commandType: 'ApprovalResponse' })

    expect(state.pendingApproval?.requestId).toBe('correlation_fixture_1')
    expect(state.commandInFlight).toBe('ApprovalResponse')
    expect(commandGateFor(state).canRespondToApproval).toBe(false)

    const duplicatePolicy = reduceSession(state, { type: 'EVENT', event: {
      ...fixture.events[1],
      sequence: 2,
      type: 'policy_decision',
      correlation_id: 'other-correlation',
      payload: { decision: 'allow' },
    } })
    expect(duplicatePolicy.pendingApproval?.requestId).toBe('correlation_fixture_1')
    expect(duplicatePolicy.commandInFlight).toBe('ApprovalResponse')

    state = reduceSession(state, { type: 'EVENT', event: {
      ...fixture.events[2],
      sequence: 2,
      type: 'policy_decision',
      correlation_id: 'correlation_fixture_1',
      payload: { decision: 'allow' },
    } })
    expect(state.pendingApproval).toBeNull()
    expect(state.commandInFlight).toBeNull()
    expect(commandGateFor(state).kind).toBe('turn_running')
  })

  it('keeps Stop locked after 202 until an INTERRUPTED turn boundary', () => {
    let state = createInitialSessionState()
    state = reduceSession(state, {
      type: 'CONNECTED',
      transportSessionId: 'transport_fixture_1',
      cursor: 0,
      state: 'RUNNING',
    })
    state = reduceSession(state, { type: 'EVENT', event: {
      ...fixture.events[0],
      sequence: 1,
      type: 'user_message',
      payload: { text: 'inspect' },
    } })
    state = reduceSession(state, { type: 'COMMAND_STARTED', commandType: 'Interrupt' })
    state = reduceSession(state, { type: 'COMMAND_ACCEPTED', commandType: 'Interrupt' })
    expect(state.commandInFlight).toBe('Interrupt')
    expect(commandGateFor(state).canInterrupt).toBe(false)

    state = reduceSession(state, { type: 'EVENT', event: {
      ...fixture.events[1],
      sequence: 2,
      type: 'turn_end',
      payload: { state: 'INTERRUPTED', reason: 'user_cancelled', assistant_text: '' },
    } })
    expect(state.status).toBe('INTERRUPTED')
    expect(state.turnActive).toBe(false)
    expect(state.commandInFlight).toBeNull()
    expect(commandGateFor(state).kind).toBe('terminal')
  })

  it('blocks approval after a stream error without treating the error as approval', () => {
    let state = createInitialSessionState()
    state = reduceSession(state, {
      type: 'CONNECTED',
      transportSessionId: 'transport_fixture_1',
      cursor: 0,
      state: 'RUNNING',
    })
    state = reduceSession(state, { type: 'EVENT', event: {
      ...fixture.events[0],
      sequence: 1,
      type: 'approval_request',
      correlation_id: 'correlation_fixture_1',
      payload: { request_id: 'correlation_fixture_1' },
    } })
    state = reduceSession(state, { type: 'STREAM_ERROR', message: 'disconnected' })
    expect(state.pendingApproval?.requestId).toBe('correlation_fixture_1')
    expect(state.streamAvailable).toBe(false)
    expect(commandGateFor(state).canRespondToApproval).toBe(false)
    expect(commandGateFor(state).canInterrupt).toBe(true)
  })

  it('keeps an invalid approval request fail-closed while exposing Stop', () => {
    let state = reduceSession(createInitialSessionState(), {
      type: 'CONNECTED',
      transportSessionId: 'transport_fixture_1',
      cursor: 0,
      state: 'RUNNING',
    })
    state = reduceSession(state, { type: 'EVENT', event: {
      ...fixture.events[0],
      sequence: 1,
      type: 'approval_request',
      correlation_id: 'correlation_fixture_1',
      payload: { request_id: 'different' },
    } })
    expect(state.pendingApproval).toBeNull()
    expect(state.turnActive).toBe(true)
    expect(commandGateFor(state).canRespondToApproval).toBe(false)
    expect(commandGateFor(state).canInterrupt).toBe(true)
  })

  it('handles timeout and ordinary tool failure as tool facts, not session failure', () => {
    let state = createInitialSessionState()
    state = reduceSession(state, {
      type: 'CONNECTED',
      transportSessionId: 'transport_fixture_1',
      cursor: 0,
      state: 'RUNNING',
    })
    const events = [
      {
        ...fixture.events[0],
        sequence: 1,
        type: 'tool_call',
        correlation_id: 'correlation_fixture_1',
        payload: { tool_name: 'bash' },
      },
      {
        ...fixture.events[1],
        sequence: 2,
        type: 'approval_request',
        correlation_id: 'correlation_fixture_1',
        payload: { request_id: 'correlation_fixture_1', tool_name: 'bash' },
      },
      {
        ...fixture.events[2],
        sequence: 3,
        type: 'policy_decision',
        correlation_id: 'correlation_fixture_1',
        payload: { decision: 'deny', reason: 'approval_timeout' },
      },
      {
        ...fixture.events[3],
        sequence: 4,
        type: 'tool_result',
        correlation_id: 'correlation_fixture_1',
        payload: { result: { status: 'timeout', text: 'timed out', timed_out: true } },
      },
      {
        ...fixture.events[3],
        sequence: 5,
        type: 'turn_end',
        correlation_id: null,
        payload: { state: 'COMPLETED_TURN', assistant_text: 'done' },
      },
    ]
    for (const event of events) {
      state = reduceSession(state, { type: 'EVENT', event })
    }

    expect(state.pendingApproval).toBeNull()
    expect(state.status).toBe('COMPLETED_TURN')
    expect(state.status).not.toBe('FAILED')
    expect(commandGateFor(state).canSubmitTask).toBe(true)
  })

  it('prioritizes terminal state after an ambiguous RUNNING reattach', () => {
    let state = reduceSession(createInitialSessionState(), {
      type: 'SESSION_ATTACHED',
      transportSessionId: 'transport_fixture_1',
      cursor: 0,
      state: 'RUNNING',
    })
    state = reduceSession(state, {
      type: 'EVENT',
      event: {
        ...fixture.events[3],
        sequence: 1,
        type: 'agent_end',
        payload: { state: 'INTERRUPTED', reason: 'user_cancelled' },
      },
    })

    expect(state.resumeStateAmbiguous).toBe(true)
    expect(state.turnActive).toBe(true)
    expect(commandGateFor(state)).toMatchObject({
      kind: 'terminal',
      canInterrupt: false,
      canSubmitTask: false,
    })

    state = reduceSession(state, {
      type: 'EVENT',
      event: {
        ...fixture.events[3],
        sequence: 2,
        type: 'session_end',
        payload: { state: 'INTERRUPTED', reason: 'user_cancelled' },
      },
    })
    expect(state.connection).toBe('closed')
    expect(state.resumeStateAmbiguous).toBe(false)
    expect(commandGateFor(state).kind).toBe('closed')
  })
})
