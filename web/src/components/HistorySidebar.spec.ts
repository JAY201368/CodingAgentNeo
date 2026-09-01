import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import fixture from '../domain/fixtures/transport-v1.json'
import { asCanonicalSessionId } from '../domain/protocol'
import type { BoundedText, HistoryDiagnostic, SessionHistoryItem } from '../domain/history'
import { parseSessionHistoryPage } from '../domain/history'
import HistorySidebar from './HistorySidebar.vue'

function boundedText(text: string, truncated = false): BoundedText {
  return {
    text,
    truncated,
    original_length: text.length,
    limit: 4096,
    encoding: 'utf-8',
  }
}

function historyItem(overrides: {
  readonly session_id?: string
} & Partial<Omit<SessionHistoryItem, 'session_id'>> = {}): SessionHistoryItem {
  const { session_id, ...rest } = overrides
  return {
    session_id: asCanonicalSessionId(session_id ?? 'session_fixture_1'),
    first_user_message: boundedText('请检查失败测试'),
    created_at: '2026-09-01T08:00:00.000000Z',
    updated_at: '2026-09-01T08:01:00.000000Z',
    last_sequence: 4,
    last_state: 'COMPLETED_TURN',
    resumable: true,
    diagnostics: [],
    ...rest,
  }
}

function mountSidebar(
  props: Partial<{
    items: readonly SessionHistoryItem[]
    loading: boolean
    error: { code: string; message: string } | null
    hasMore: boolean
    activeSessionId: string | null
    switching: boolean
  }> = {},
  attachTo?: HTMLElement,
) {
  return mount(HistorySidebar, {
    props: {
      items: [],
      loading: false,
      error: null,
      hasMore: false,
      activeSessionId: null,
      switching: false,
      ...props,
    },
    attachTo,
  })
}

describe('HistorySidebar', () => {
  it('renders summaries, timestamps, states, and resumable flags in list order', () => {
    const first = historyItem({
      session_id: 'session_fixture_1',
      first_user_message: boundedText('first summary'),
      updated_at: '2026-09-01T08:01:00.000000Z',
      last_state: 'COMPLETED_TURN',
      resumable: true,
    })
    const second = historyItem({
      session_id: 'session_fixture_2',
      first_user_message: boundedText('second summary'),
      created_at: '2026-09-01T07:00:00.000000Z',
      updated_at: null,
      last_state: 'WAITING_FOR_INPUT',
      resumable: false,
    })
    const wrapper = mountSidebar({ items: [first, second] })

    expect(wrapper.find('[role="list"]').exists()).toBe(true)
    expect(wrapper.findAll('[role="listitem"]')).toHaveLength(2)
    expect(wrapper.findAll('.history-sidebar__summary').map((node) => node.text())).toEqual([
      'first summary',
      'second summary',
    ])
    expect(wrapper.findAll('.history-sidebar__time').map((node) => node.text())).toEqual([
      '2026-09-01T08:01:00.000000Z',
      '2026-09-01T07:00:00.000000Z',
    ])
    expect(wrapper.findAll('.history-sidebar__state').map((node) => node.text())).toEqual([
      'COMPLETED_TURN',
      'WAITING_FOR_INPUT',
    ])
    expect(wrapper.findAll('.history-sidebar__resume').map((node) => node.text())).toEqual([
      '可恢复',
      '不可恢复',
    ])
    expect(wrapper.text()).toContain('不可恢复')
  })

  it('renders the shared history fixture without executing markup', () => {
    const page = parseSessionHistoryPage(fixture.history.list)
    const wrapper = mountSidebar({ items: page.sessions })
    expect(wrapper.text()).toContain('请检查失败测试')
    expect(wrapper.text()).toContain('COMPLETED_TURN')
    expect(wrapper.text()).toContain('可恢复')
    expect(wrapper.html()).not.toContain('v-html')
  })

  it('keeps non-resumable items visible and does not emit select', async () => {
    const blocked = historyItem({
      session_id: 'session_fixture_blocked',
      first_user_message: boundedText('blocked task'),
      resumable: false,
    })
    const wrapper = mountSidebar({ items: [blocked] })
    const button = wrapper.get('.history-sidebar__select')

    expect(wrapper.text()).toContain('blocked task')
    expect(wrapper.text()).toContain('不可恢复')
    expect((button.element as HTMLButtonElement).disabled).toBe(true)

    await button.trigger('click')
    expect(wrapper.emitted('select')).toBeUndefined()
  })

  it('emits select(session_id) for a resumable item', async () => {
    const item = historyItem({ session_id: 'session_fixture_1' })
    const wrapper = mountSidebar({ items: [item] })
    await wrapper.get('.history-sidebar__select').trigger('click')
    expect(wrapper.emitted('select')).toEqual([['session_fixture_1']])
  })

  it('marks the active session with aria-current', () => {
    const items = [
      historyItem({ session_id: 'session_fixture_1', first_user_message: boundedText('one') }),
      historyItem({ session_id: 'session_fixture_2', first_user_message: boundedText('two') }),
    ]
    const wrapper = mountSidebar({
      items,
      activeSessionId: 'session_fixture_2',
    })
    const buttons = wrapper.findAll('.history-sidebar__select')
    expect(buttons[0]?.attributes('aria-current')).toBeUndefined()
    expect(buttons[1]?.attributes('aria-current')).toBe('true')
    expect(wrapper.findAll('.history-sidebar__item--current')).toHaveLength(1)
    expect(wrapper.get('.history-sidebar__item--current').text()).toContain('two')
  })

  it('disables selection while switching and does not emit', async () => {
    const wrapper = mountSidebar({
      items: [historyItem()],
      switching: true,
    })
    const button = wrapper.get('.history-sidebar__select')
    expect((button.element as HTMLButtonElement).disabled).toBe(true)
    await button.trigger('click')
    expect(wrapper.emitted('select')).toBeUndefined()
  })

  it('shows load more when hasMore and emits loadMore', async () => {
    const wrapper = mountSidebar({
      items: [historyItem()],
      hasMore: true,
    })
    const loadMore = wrapper.get('.history-sidebar__load-more')
    expect(loadMore.text()).toBe('加载更多')
    expect(loadMore.attributes('aria-label')).toBe('加载更多历史 session')
    await loadMore.trigger('click')
    expect(wrapper.emitted('loadMore')).toEqual([[]])
  })

  it('hides load more when hasMore is false and disables it while switching', async () => {
    const hidden = mountSidebar({ items: [historyItem()], hasMore: false })
    expect(hidden.find('.history-sidebar__load-more').exists()).toBe(false)

    const wrapper = mountSidebar({
      items: [historyItem()],
      hasMore: true,
      switching: true,
    })
    const loadMore = wrapper.get('.history-sidebar__load-more')
    expect((loadMore.element as HTMLButtonElement).disabled).toBe(true)
    await loadMore.trigger('click')
    expect(wrapper.emitted('loadMore')).toBeUndefined()
  })

  it('shows a distinct empty state', () => {
    const wrapper = mountSidebar({
      items: [],
      loading: false,
      error: null,
    })
    expect(wrapper.text()).toContain('还没有历史 session')
    expect(wrapper.find('[role="list"]').exists()).toBe(false)
    expect(wrapper.find('[role="status"]').attributes('aria-live')).toBe('polite')
  })

  it('shows a loading state when loading with no items', () => {
    const wrapper = mountSidebar({
      items: [],
      loading: true,
    })
    expect(wrapper.attributes('aria-busy')).toBe('true')
    expect(wrapper.text()).toContain('正在加载历史 session')
    expect(wrapper.find('[role="list"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('还没有历史 session')
  })

  it('shows a safe error with a refresh control even when items remain', async () => {
    const wrapper = mountSidebar({
      items: [historyItem({ first_user_message: boundedText('kept page') })],
      error: { code: 'history_unavailable', message: '历史记录暂时不可用' },
    })
    expect(wrapper.text()).toContain('历史记录暂时不可用')
    expect(wrapper.text()).toContain('kept page')
    expect(wrapper.find('[role="list"]').exists()).toBe(true)

    const refresh = wrapper.get('.history-sidebar__refresh')
    expect(refresh.text()).toBe('重试')
    expect(refresh.attributes('aria-label')).toBe('刷新历史列表')
    await refresh.trigger('click')
    expect(wrapper.emitted('refresh')).toEqual([[]])
  })

  it('treats error !== null as the error state even with an empty list', () => {
    const wrapper = mountSidebar({
      items: [],
      loading: false,
      error: { code: 'history_not_found', message: '找不到该历史记录' },
    })
    expect(wrapper.text()).toContain('找不到该历史记录')
    expect(wrapper.text()).not.toContain('还没有历史 session')
    expect(wrapper.find('.history-sidebar__refresh').exists()).toBe(true)
  })

  it('renders untrusted summary text as plain text and never uses v-html', () => {
    const markup = '<img src="x" onerror="alert(1)"><b>inject</b>'
    const wrapper = mountSidebar({
      items: [historyItem({ first_user_message: boundedText(markup) })],
    })
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('b').exists()).toBe(false)
    expect(wrapper.text()).toContain(markup)
    expect(wrapper.html()).not.toContain('v-html')
  })

  it('shows diagnostic codes quietly and does not render diagnostic messages', () => {
    const diagnostics: readonly HistoryDiagnostic[] = [
      { code: 'truncated_payload', message: '/var/secret/session.jsonl' },
    ]
    const wrapper = mountSidebar({
      items: [historyItem({ diagnostics })],
    })
    expect(wrapper.text()).toContain('truncated_payload')
    expect(wrapper.text()).not.toContain('/var/secret/session.jsonl')
  })

  it('lets the keyboard focus resumable items and load more', async () => {
    const wrapper = mountSidebar(
      {
        items: [historyItem()],
        hasMore: true,
      },
      document.body,
    )
    await nextTick()

    const selectButton = wrapper.get('.history-sidebar__select').element as HTMLButtonElement
    const loadMore = wrapper.get('.history-sidebar__load-more').element as HTMLButtonElement
    selectButton.focus()
    expect(document.activeElement).toBe(selectButton)
    loadMore.focus()
    expect(document.activeElement).toBe(loadMore)
    expect(selectButton.textContent).toContain('请检查失败测试')
    expect(loadMore.getAttribute('aria-label')).toBe('加载更多历史 session')
    wrapper.unmount()
  })
})
