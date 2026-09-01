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

    expect(items.map((item) => item.sequence)).toEqual([1, 2, 3, 4])
    expect(items.map((item) => item.kind)).toEqual(['run', 'assistant', 'run', 'end'])
    expect(items[1].text).toBe('done')
    expect(items[2].title).toContain('未知事件')
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
})
