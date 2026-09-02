<script setup lang="ts">
import { computed } from 'vue'

import { safeDisplayText } from '../domain/events'
import type { SessionHistoryItem } from '../domain/history'

const props = withDefaults(defineProps<{
  readonly items: readonly SessionHistoryItem[]
  readonly loading: boolean
  readonly error: { readonly code: string; readonly message: string } | null
  readonly hasMore: boolean
  readonly activeSessionId: string | null
  readonly switching: boolean
  readonly lifecycleBusy?: 'create' | 'resume' | null
}>(), {
  lifecycleBusy: null,
})

const emit = defineEmits<{
  create: []
  select: [session_id: string]
  loadMore: []
  refresh: []
}>()

const showInitialLoading = computed(() => props.loading && props.items.length === 0)
const visibleItems = computed(() => {
  const resumable = props.items.filter((item) => item.resumable)
  return [...resumable].sort((left, right) => itemCreatedMs(right) - itemCreatedMs(left))
})
const showEmpty = computed(() =>
  !props.loading && props.error === null && visibleItems.value.length === 0,
)
const busy = computed(() => props.switching || props.lifecycleBusy !== null)
const busyStatus = computed(() =>
  props.lifecycleBusy === 'create' ? '正在新建 session…' : '正在切换 session…',
)

function itemCreatedMs(item: SessionHistoryItem): number {
  const raw = item.created_at ?? item.updated_at
  if (raw === null) {
    return 0
  }
  const ms = Date.parse(raw)
  return Number.isNaN(ms) ? 0 : ms
}

function itemSummary(item: SessionHistoryItem): string {
  const text = safeDisplayText(item.first_user_message.text, 240).trim()
  return text.length > 0 ? text : '（无首条用户消息）'
}

function formatBeijingTime(value: string | null): string {
  if (value === null || value.trim().length === 0) {
    return '时间未知'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '时间未知'
  }
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(date)
  const read = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((part) => part.type === type)?.value ?? ''
  return `${read('year')}-${read('month')}-${read('day')} ${read('hour')}:${read('minute')}`
}

function itemTimestamp(item: SessionHistoryItem): string {
  return formatBeijingTime(item.created_at ?? item.updated_at)
}

function isActive(item: SessionHistoryItem): boolean {
  return props.activeSessionId === item.session_id
}

function canSelect(): boolean {
  return !busy.value
}

function createSession(): void {
  if (busy.value) {
    return
  }
  emit('create')
}

function selectItem(item: SessionHistoryItem): void {
  if (!canSelect()) {
    return
  }
  emit('select', item.session_id)
}

function loadMore(): void {
  if (busy.value) {
    return
  }
  emit('loadMore')
}

function refresh(): void {
  emit('refresh')
}
</script>

<template>
  <aside
    id="history-sidebar"
    class="history-sidebar"
    aria-label="历史 session"
    tabindex="-1"
    :aria-busy="loading || busy"
  >
    <header class="history-sidebar__header">
      <div
        v-if="$slots.title"
        class="history-sidebar__heading"
      >
        <slot name="title" />
      </div>
      <button
        class="history-sidebar__create"
        type="button"
        aria-label="新建 session"
        :disabled="busy"
        @click="createSession"
      >
        <svg
          class="history-sidebar__create-mark"
          viewBox="0 0 16 16"
          width="14"
          height="14"
          aria-hidden="true"
          focusable="false"
        >
          <path
            fill="currentColor"
            d="M7 2h2v12H7zM2 7h12v2H2z"
          />
        </svg>
      </button>
    </header>

    <div
      v-if="error !== null"
      class="history-sidebar__error"
    >
      <p
        class="history-sidebar__error-message"
        role="status"
        aria-live="polite"
      >
        <span
          class="alert__mark"
          aria-hidden="true"
        >!</span>
        <span>{{ error.message }}</span>
      </p>
      <button
        class="secondary-action history-sidebar__refresh"
        type="button"
        aria-label="刷新历史列表"
        @click="refresh"
      >
        重试
      </button>
    </div>

    <p
      v-if="busy"
      class="history-sidebar__status history-sidebar__switching"
      role="status"
      aria-live="polite"
    >
      {{ busyStatus }}
    </p>

    <p
      v-if="showInitialLoading"
      class="history-sidebar__status loading-note"
      role="status"
      aria-live="polite"
    >
      <span
        class="loading-mark"
        aria-hidden="true"
      >◌</span>
      <span>正在加载历史 session…</span>
    </p>

    <p
      v-else-if="showEmpty"
      class="history-sidebar__status history-sidebar__empty"
      role="status"
      aria-live="polite"
    >
      <span
        class="history-sidebar__empty-mark"
        aria-hidden="true"
      >–</span>
      <span>还没有历史 session。</span>
    </p>

    <ul
      v-if="visibleItems.length > 0"
      class="history-sidebar__list"
      role="list"
    >
      <li
        v-for="item in visibleItems"
        :key="item.session_id"
        class="history-sidebar__item"
        :class="{ 'history-sidebar__item--current': isActive(item) }"
        role="listitem"
      >
        <button
          class="history-sidebar__select"
          type="button"
          :disabled="!canSelect()"
          :aria-current="isActive(item) ? 'true' : undefined"
          @click="selectItem(item)"
        >
          <span class="history-sidebar__summary">{{ itemSummary(item) }}</span>
          <span class="history-sidebar__meta">
            <span
              v-if="isActive(item)"
              class="history-sidebar__current-mark"
            >当前</span>
            <span class="history-sidebar__time">{{ itemTimestamp(item) }}</span>
          </span>
        </button>
      </li>
    </ul>

    <button
      v-if="hasMore"
      class="secondary-action history-sidebar__load-more"
      type="button"
      :disabled="busy"
      aria-label="加载更多历史 session"
      @click="loadMore"
    >
      加载更多
    </button>
  </aside>
</template>
