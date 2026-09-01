import fixture from './fixtures/transport-v1.json'
import { describe, expect, it } from 'vitest'

import {
  asCanonicalSessionId,
  asTransportSessionId,
} from './protocol'
import type { CanonicalSessionId, TransportSessionId } from './protocol'
import {
  HISTORY_LIST_MAX_SESSIONS,
  isCanonicalSessionId,
  isHistoryEventLimit,
  isHistoryListLimit,
  isHistorySince,
  isOpaqueHistoryListCursor,
  parseBoundedText,
  parseSessionEventPage,
  parseSessionHistoryPage,
  parseTruncatedPayloadPreview,
} from './history'

type AssertDistinct<A, B> = [A] extends [B] ? ([B] extends [A] ? never : true) : true

describe('history DTO parsers', () => {
  it('keeps transport and canonical session IDs as distinct nominal types', () => {
    const distinct: AssertDistinct<CanonicalSessionId, TransportSessionId> = true
    expect(distinct).toBe(true)
    const canonical = asCanonicalSessionId(fixture.history.list.sessions[0].session_id)
    const transport = asTransportSessionId(fixture.history.resume.response.transport_session_id)
    expect(canonical.startsWith('session_')).toBe(true)
    expect(transport.startsWith('transport_')).toBe(true)
  })

  it('parses the shared list fixture with BoundedText and opaque next_cursor', () => {
    const page = parseSessionHistoryPage(fixture.history.list)
    expect(page.sessions).toHaveLength(1)
    expect(page.next_cursor).toBe('opaque_history_cursor_fixture_1')
    const item = page.sessions[0]
    expect(item).toMatchObject({
      session_id: 'session_fixture_1',
      created_at: '2026-09-01T08:00:00.000000Z',
      updated_at: '2026-09-01T08:01:00.000000Z',
      last_sequence: 4,
      last_state: 'COMPLETED_TURN',
      resumable: true,
      diagnostics: [],
    })
    expect(item?.first_user_message).toEqual(fixture.history.list.sessions[0].first_user_message)
  })

  it('parses shared event pages, empty pages, and truncated payload previews', () => {
    const page = parseSessionEventPage(fixture.history.events)
    expect(page.session_id).toBe('session_fixture_1')
    expect(page.has_more).toBe(true)
    expect(page.next_cursor).toBe(2)
    expect(page.events.map((event) => event.sequence)).toEqual([1, 2])
    expect(page.events[0]?.type).toBe('session_start')
    expect(page.events[1]?.payload).toMatchObject({ text: 'done', new_field: { preserve: true } })

    const empty = parseSessionEventPage(fixture.history.events_empty)
    expect(empty.events).toEqual([])
    expect(empty.has_more).toBe(false)
    expect(empty.next_cursor).toBeNull()

    expect(parseTruncatedPayloadPreview(fixture.history.truncated_payload)).toEqual({
      truncated: true,
      original_length: 123456,
      limit: 65536,
      encoding: 'utf-8',
      head: 'head-preview',
      tail: 'tail-preview',
    })
  })

  it('ignores unknown fields and degrades missing, illegal, truncated, and oversized input', () => {
    expect(() => parseSessionHistoryPage(null)).not.toThrow()
    expect(parseSessionHistoryPage(null)).toEqual({ sessions: [], next_cursor: null })
    expect(parseSessionHistoryPage('nope')).toEqual({ sessions: [], next_cursor: null })

    const page = parseSessionHistoryPage({
      extra: true,
      next_cursor: { not: 'a token' },
      sessions: [
        {
          session_id: 'session_ok1',
          unknown_field: 'ignored',
          first_user_message: {
            text: 'hello',
            truncated: true,
            original_length: 5000,
            limit: 4096,
            encoding: 'utf-8',
            extra: 'ignored',
          },
          created_at: 12,
          updated_at: '',
          last_sequence: '7',
          last_state: 'FUTURE_STATE',
          resumable: 'yes',
          diagnostics: [
            { code: 'incomplete_tail', message: 'history has an incomplete final record' },
            null,
            'bad',
            { message: 'missing code' },
            { code: 'kept', message: 11 },
          ],
        },
        { session_id: 'transport_fixture_1' },
        { not: 'an item' },
        'skip-me',
        {
          session_id: 'session_ok2',
          first_user_message: 'plain text',
        },
      ],
    })
    expect(page.next_cursor).toBeNull()
    expect(page.sessions.map((item) => item.session_id)).toEqual(['session_ok1', 'session_ok2'])
    expect(page.sessions[0]?.resumable).toBe(false)
    expect(page.sessions[0]?.created_at).toBeNull()
    expect(page.sessions[0]?.last_sequence).toBeNull()
    expect(page.sessions[0]?.last_state).toBe('FUTURE_STATE')
    expect(page.sessions[0]?.first_user_message.truncated).toBe(true)
    expect(page.sessions[0]?.diagnostics).toEqual([
      { code: 'incomplete_tail', message: 'history has an incomplete final record' },
      { code: 'kept', message: '' },
    ])
    expect(page.sessions[1]?.first_user_message).toMatchObject({
      text: 'plain text',
      truncated: false,
    })
  })

  it('caps sessions at 100 and does not let one bad item fail the page', () => {
    const sessions = Array.from({ length: HISTORY_LIST_MAX_SESSIONS + 5 }, (_, index) => ({
      session_id: `session_item${index}`,
    }))
    sessions.splice(1, 0, { session_id: '../outside' } as never)
    const page = parseSessionHistoryPage({ sessions, next_cursor: null })
    expect(page.sessions).toHaveLength(HISTORY_LIST_MAX_SESSIONS)
    expect(page.sessions[0]?.session_id).toBe('session_item0')
  })

  it('sorts valid events, drops bad envelopes, and forces empty-page cursors', () => {
    expect(() => parseSessionEventPage(undefined)).not.toThrow()
    const page = parseSessionEventPage({
      session_id: 'session_fixture_1',
      extra: 'ignored',
      has_more: 'yes',
      next_cursor: '2',
      diagnostics: [{ code: 'incomplete_tail', message: 'tail' }, { broken: true }],
      events: [
        fixture.history.events.events[1],
        { schema_version: 1, sequence: 'nope', type: 'assistant_message', payload: {} },
        fixture.history.events.events[0],
        {
          schema_version: 1,
          session_id: 'session_fixture_1',
          event_id: 'event_truncated_1',
          agent_id: 'agent_fixture_1',
          parent_agent_id: null,
          sequence: 3,
          type: 'assistant_message',
          correlation_id: null,
          provider_tool_call_id: null,
          timestamp: '2026-08-31T08:00:00.223456Z',
          payload: fixture.history.truncated_payload,
        },
      ],
    })
    expect(page.events.map((event) => event.sequence)).toEqual([1, 2, 3])
    expect(page.has_more).toBe(false)
    expect(page.next_cursor).toBeNull()
    expect(page.diagnostics.some((item) => item.code === 'incomplete_tail')).toBe(true)
    expect(page.events[2]?.payload).toMatchObject({ truncated: true, head: 'head-preview' })
    expect(parseTruncatedPayloadPreview(page.events[2]?.payload)).toMatchObject({
      truncated: true,
      head: 'head-preview',
      tail: 'tail-preview',
    })

    const empty = parseSessionEventPage({
      session_id: 'session_fixture_1',
      events: [],
      has_more: true,
      next_cursor: 9,
      diagnostics: [],
    })
    expect(empty.has_more).toBe(false)
    expect(empty.next_cursor).toBeNull()
  })

  it('parses BoundedText fallbacks without throwing', () => {
    expect(parseBoundedText(null)).toMatchObject({ text: '', truncated: false, encoding: 'utf-8' })
    expect(parseBoundedText({ truncated: true })).toMatchObject({ text: '', truncated: true })
    expect(parseBoundedText({ text: 'ok', original_length: -1, limit: 0 })).toMatchObject({
      text: 'ok',
      original_length: 2,
      limit: 4096,
    })
  })
})

describe('history ID, cursor, limit, and since guards', () => {
  it('accepts opaque session_ tokens and rejects path-like or transport IDs', () => {
    expect(isCanonicalSessionId('session_fixture_1')).toBe(true)
    expect(isCanonicalSessionId('session_0123456789abcdef0123456789abcdef')).toBe(true)
    const rejected = [
      'transport_fixture_1',
      '../outside',
      'session_foo/bar',
      'session_foo\\bar',
      'session_foo.',
      'session_foo.jsonl',
      'session_foo.jsonl.bak',
      'session_',
      'session_foo\u0000bar',
      'session_foo\nbar',
      '/tmp/session_x',
      'session_..hidden',
      '',
    ]
    for (const value of rejected) {
      expect(isCanonicalSessionId(value)).toBe(false)
    }
  })

  it('treats list cursors as opaque ASCII and validates limit/since bounds', () => {
    expect(isOpaqueHistoryListCursor('opaque_history_cursor_fixture_1')).toBe(true)
    expect(isOpaqueHistoryListCursor('a+b/c&=')).toBe(true)
    expect(isOpaqueHistoryListCursor('')).toBe(false)
    expect(isOpaqueHistoryListCursor('x'.repeat(257))).toBe(false)
    expect(isOpaqueHistoryListCursor('cursor-你好')).toBe(false)
    expect(isOpaqueHistoryListCursor('cursor\n')).toBe(false)
    expect(isHistoryListLimit(50)).toBe(true)
    expect(isHistoryListLimit(1)).toBe(true)
    expect(isHistoryListLimit(100)).toBe(true)
    expect(isHistoryListLimit(0)).toBe(false)
    expect(isHistoryListLimit(101)).toBe(false)
    expect(isHistoryListLimit(50.5)).toBe(false)
    expect(isHistoryEventLimit(200)).toBe(true)
    expect(isHistoryEventLimit(201)).toBe(false)
    expect(isHistorySince(0)).toBe(true)
    expect(isHistorySince(Number.MAX_SAFE_INTEGER)).toBe(true)
    expect(isHistorySince(-1)).toBe(false)
    expect(isHistorySince(1.2)).toBe(false)
  })
})
