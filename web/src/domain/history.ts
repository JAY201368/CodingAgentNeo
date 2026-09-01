/**
 * Defensive parsers for workspace history list/event DTOs.
 *
 * History IDs are canonical `session_...` tokens, never transport session IDs.
 * Parsers ignore unknown fields, degrade bad candidates, and do not throw.
 */

import {
  asCanonicalSessionId,
  isNonNegativeInteger,
  isRecord,
} from './protocol'
import type { AgentEventEnvelope, CanonicalSessionId } from './protocol'
import { parseEventEnvelopeResult } from './events'
import type { EventDiagnostic } from './events'

export const HISTORY_LIST_DEFAULT_LIMIT = 50
export const HISTORY_LIST_MAX_LIMIT = 100
export const HISTORY_LIST_MAX_SESSIONS = 100
export const HISTORY_EVENT_DEFAULT_LIMIT = 200
export const HISTORY_EVENT_MAX_LIMIT = 200
export const HISTORY_CURSOR_MAX_LENGTH = 256
export const HISTORY_SESSION_ID_MAX_LENGTH = 256
export const HISTORY_ITEM_MAX_DIAGNOSTICS = 8
export const HISTORY_PAGE_MAX_DIAGNOSTICS = 64
export const BOUNDED_TEXT_DEFAULT_LIMIT = 4096
export const BOUNDED_TEXT_DEFAULT_ENCODING = 'utf-8'

/**
 * Wire `since` is 0..2^63-1. JavaScript numbers cannot represent that full
 * range exactly, so the browser accepts the safe-integer subset.
 */
export const HISTORY_SINCE_MAX = Number.MAX_SAFE_INTEGER

const CANONICAL_SESSION_REST = /^[A-Za-z0-9][A-Za-z0-9_-]*$/

export interface BoundedText {
  readonly text: string
  readonly truncated: boolean
  readonly original_length: number
  readonly limit: number
  readonly encoding: string
}

export interface TruncatedPayloadPreview {
  readonly truncated: true
  readonly original_length: number | null
  readonly limit: number | null
  readonly encoding: string | null
  readonly head: string
  readonly tail: string
}

export interface HistoryDiagnostic {
  readonly code: string
  readonly message: string
}

export interface SessionHistoryItem {
  readonly session_id: CanonicalSessionId
  readonly first_user_message: BoundedText
  readonly created_at: string | null
  readonly updated_at: string | null
  readonly last_sequence: number | null
  readonly last_state: string | null
  readonly resumable: boolean
  readonly diagnostics: readonly HistoryDiagnostic[]
}

export interface SessionHistoryPage {
  readonly sessions: readonly SessionHistoryItem[]
  readonly next_cursor: string | null
}

export interface SessionEventPage {
  readonly session_id: CanonicalSessionId | null
  readonly events: readonly AgentEventEnvelope[]
  readonly next_cursor: number | null
  readonly has_more: boolean
  readonly diagnostics: readonly HistoryDiagnostic[]
}

const EMPTY_BOUNDED_TEXT: BoundedText = {
  text: '',
  truncated: false,
  original_length: 0,
  limit: BOUNDED_TEXT_DEFAULT_LIMIT,
  encoding: BOUNDED_TEXT_DEFAULT_ENCODING,
}

export function isCanonicalSessionId(value: unknown): value is CanonicalSessionId {
  if (typeof value !== 'string' || value.length === 0) {
    return false
  }
  if (value.length > HISTORY_SESSION_ID_MAX_LENGTH) {
    return false
  }
  if (value.includes('/') || value.includes('\\')) {
    return false
  }
  if (value.endsWith('.') || value.toLowerCase().endsWith('.jsonl')) {
    return false
  }
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index)
    if (code <= 0x1f || code === 0x7f) {
      return false
    }
  }
  if (!value.startsWith('session_')) {
    return false
  }
  const rest = value.slice('session_'.length)
  if (rest.length === 0 || rest.includes('.') || rest.includes('..')) {
    return false
  }
  return CANONICAL_SESSION_REST.test(rest)
}

export function isOpaqueHistoryListCursor(value: unknown): value is string {
  if (typeof value !== 'string') {
    return false
  }
  if (value.length < 1 || value.length > HISTORY_CURSOR_MAX_LENGTH) {
    return false
  }
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index)
    if (code < 0x20 || code > 0x7e) {
      return false
    }
  }
  return true
}

export function isHistoryListLimit(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= HISTORY_LIST_MAX_LIMIT
}

export function isHistoryEventLimit(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= HISTORY_EVENT_MAX_LIMIT
}

export function isHistorySince(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isInteger(value) &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= HISTORY_SINCE_MAX
  )
}

export function parseBoundedText(value: unknown): BoundedText {
  if (typeof value === 'string') {
    return {
      text: value,
      truncated: false,
      original_length: value.length,
      limit: BOUNDED_TEXT_DEFAULT_LIMIT,
      encoding: BOUNDED_TEXT_DEFAULT_ENCODING,
    }
  }
  if (!isRecord(value)) {
    return EMPTY_BOUNDED_TEXT
  }
  const text = typeof value.text === 'string' ? value.text : ''
  const originalLength = isNonNegativeInteger(value.original_length)
    ? value.original_length
    : text.length
  const limit = isNonNegativeInteger(value.limit) && value.limit > 0
    ? value.limit
    : BOUNDED_TEXT_DEFAULT_LIMIT
  const encoding =
    typeof value.encoding === 'string' && value.encoding.trim().length > 0
      ? value.encoding
      : BOUNDED_TEXT_DEFAULT_ENCODING
  return {
    text,
    truncated: value.truncated === true,
    original_length: originalLength,
    limit,
    encoding,
  }
}

export function parseTruncatedPayloadPreview(value: unknown): TruncatedPayloadPreview | null {
  if (!isRecord(value) || value.truncated !== true) {
    return null
  }
  return {
    truncated: true,
    original_length: isNonNegativeInteger(value.original_length) ? value.original_length : null,
    limit: isNonNegativeInteger(value.limit) ? value.limit : null,
    encoding: typeof value.encoding === 'string' ? value.encoding : null,
    head: typeof value.head === 'string' ? value.head : '',
    tail: typeof value.tail === 'string' ? value.tail : '',
  }
}

function parseHistoryDiagnostic(value: unknown): HistoryDiagnostic | null {
  if (!isRecord(value)) {
    return null
  }
  if (typeof value.code !== 'string' || value.code.trim().length === 0) {
    return null
  }
  return {
    code: value.code,
    message: typeof value.message === 'string' ? value.message : '',
  }
}

function parseHistoryDiagnostics(value: unknown, maxItems: number): HistoryDiagnostic[] {
  if (!Array.isArray(value)) {
    return []
  }
  const diagnostics: HistoryDiagnostic[] = []
  for (const candidate of value) {
    if (diagnostics.length >= maxItems) {
      break
    }
    const parsed = parseHistoryDiagnostic(candidate)
    if (parsed !== null) {
      diagnostics.push(parsed)
    }
  }
  return diagnostics
}

function parseTimestamp(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

function parseHistoryItem(value: unknown): SessionHistoryItem | null {
  if (!isRecord(value) || !isCanonicalSessionId(value.session_id)) {
    return null
  }
  const lastState =
    typeof value.last_state === 'string' && value.last_state.trim().length > 0
      ? value.last_state
      : null
  return {
    session_id: asCanonicalSessionId(value.session_id),
    first_user_message: parseBoundedText(value.first_user_message),
    created_at: parseTimestamp(value.created_at),
    updated_at: parseTimestamp(value.updated_at),
    last_sequence: isNonNegativeInteger(value.last_sequence) ? value.last_sequence : null,
    last_state: lastState,
    resumable: value.resumable === true,
    diagnostics: parseHistoryDiagnostics(value.diagnostics, HISTORY_ITEM_MAX_DIAGNOSTICS),
  }
}

function parseListNextCursor(value: unknown): string | null {
  if (value === null || value === undefined) {
    return null
  }
  return isOpaqueHistoryListCursor(value) ? value : null
}

/**
 * Parse a session-history list page. Completely unusable input degrades to an
 * empty page; a single bad item never fails the rest of the list.
 */
export function parseSessionHistoryPage(input: unknown): SessionHistoryPage {
  if (!isRecord(input)) {
    return { sessions: [], next_cursor: null }
  }
  const sessions: SessionHistoryItem[] = []
  if (Array.isArray(input.sessions)) {
    for (const candidate of input.sessions) {
      if (sessions.length >= HISTORY_LIST_MAX_SESSIONS) {
        break
      }
      const item = parseHistoryItem(candidate)
      if (item !== null) {
        sessions.push(item)
      }
    }
  }
  return {
    sessions,
    next_cursor: parseListNextCursor(input.next_cursor),
  }
}

function diagnosticFromEvent(diagnostic: EventDiagnostic): HistoryDiagnostic {
  return { code: diagnostic.code, message: diagnostic.message }
}

/**
 * Parse a finite history event page. Bad envelopes/diagnostics are dropped or
 * recorded; the page itself always returns. Empty pages force has_more=false
 * and next_cursor=null.
 */
export function parseSessionEventPage(input: unknown): SessionEventPage {
  if (!isRecord(input)) {
    return {
      session_id: null,
      events: [],
      next_cursor: null,
      has_more: false,
      diagnostics: [],
    }
  }

  const sessionId = isCanonicalSessionId(input.session_id)
    ? asCanonicalSessionId(input.session_id)
    : null
  const diagnostics = parseHistoryDiagnostics(input.diagnostics, HISTORY_PAGE_MAX_DIAGNOSTICS)
  const events: AgentEventEnvelope[] = []

  if (Array.isArray(input.events)) {
    const capped = input.events.slice(0, HISTORY_EVENT_MAX_LIMIT)
    for (const candidate of capped) {
      const parsed = parseEventEnvelopeResult(candidate)
      if (parsed.envelope === null) {
        for (const diagnostic of parsed.diagnostics) {
          if (diagnostics.length >= HISTORY_PAGE_MAX_DIAGNOSTICS) {
            break
          }
          diagnostics.push(diagnosticFromEvent(diagnostic))
        }
        continue
      }
      events.push(parsed.envelope)
    }
  }

  events.sort((left, right) => left.sequence - right.sequence)

  const hasMore = input.has_more === true
  let nextCursor = isNonNegativeInteger(input.next_cursor) ? input.next_cursor : null
  if (events.length === 0) {
    return {
      session_id: sessionId,
      events,
      next_cursor: null,
      has_more: false,
      diagnostics,
    }
  }
  if (!hasMore) {
    nextCursor = null
  } else if (nextCursor === null) {
    nextCursor = events[events.length - 1]?.sequence ?? null
  }

  return {
    session_id: sessionId,
    events,
    next_cursor: nextCursor,
    has_more: hasMore,
    diagnostics,
  }
}
