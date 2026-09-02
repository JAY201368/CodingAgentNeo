import fixture from './fixtures/transport-v1.json'
import { describe, expect, it } from 'vitest'

import { parseEventEnvelope } from './events'
import { projectTimeline } from './timeline'

function envelopeAt(index: number) {
  const envelope = parseEventEnvelope(fixture.events[index])
  if (envelope === null) {
    throw new Error('fixture event was not valid')
  }
  return envelope
}

describe('timeline projection', () => {
  it('keeps sequence order and presents user, assistant, run, unknown, and end facts', () => {
    const events = [
      envelopeAt(3),
      envelopeAt(2),
      envelopeAt(1),
      envelopeAt(0),
    ]
    const items = projectTimeline(events)

    expect(items.map((item) => item.sequence)).toEqual([2, 3, 4])
    expect(items.map((item) => item.kind)).toEqual(['assistant', 'run', 'end'])
    expect(items[0].text).toBe('done')
    expect(items[1].title).toContain('未知事件')
  })

  it('omits session/agent lifecycle events from the display projection', () => {
    const base = envelopeAt(1)
    const items = projectTimeline([
      { ...base, sequence: 1, type: 'session_start', payload: { state: 'RUNNING' } },
      { ...base, sequence: 2, type: 'agent_start', payload: { state: 'RUNNING', active_tools: ['read_file'] } },
      { ...base, sequence: 3, type: 'assistant_message', payload: { text: 'model draft' } },
      { ...base, sequence: 4, type: 'tool_call', payload: { tool_name: 'read_file' } },
      { ...base, sequence: 5, type: 'policy_decision', payload: { decision: 'allow' } },
      { ...base, sequence: 6, type: 'tool_result', payload: { result: { status: 'success', text: 'ok' } } },
      { ...base, sequence: 7, type: 'agent_end', payload: { state: 'COMPLETED_TURN' } },
      { ...base, sequence: 8, type: 'session_end', payload: { state: 'COMPLETED_TURN' } },
    ])

    expect(items.map((item) => item.event.type)).toEqual([
      'assistant_message',
      'tool_call',
      'policy_decision',
      'tool_result',
    ])
    expect(items.map((item) => item.title)).toEqual([
      'Assistant 回复',
      '工具调用',
      '策略决定',
      '工具结果',
    ])
    expect(items.some((item) => item.title.includes('Session') || item.title.includes('Agent'))).toBe(false)
  })

  it('marks truncated payloads and does not stringify untrusted objects', () => {
    const event = envelopeAt(1)
    const items = projectTimeline([
      {
        ...event,
        sequence: 5,
        payload: { truncated: true, head: 'head', tail: 'tail', secret: { execute: true } },
      },
      {
        ...event,
        sequence: 6,
        payload: { text: { execute: true } },
      },
    ])

    expect(items[0].truncated).toBe(true)
    expect(items[0].text).toContain('内容已截断')
    expect(items[1].text).toBe('Assistant 文本不可用')
  })

  it('shows canonical turn-end assistant text in the timeline', () => {
    const event = envelopeAt(3)
    const [item] = projectTimeline([{
      ...event,
      type: 'turn_end',
      payload: { state: 'COMPLETED_TURN', assistant_text: 'canonical answer' },
    }])

    expect(item.text).toBe('canonical answer')
  })
})
