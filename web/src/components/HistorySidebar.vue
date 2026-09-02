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
const showEmpty = computed(() =>
  !props.loading && props.error === null && props.items.length === 0,
)
const busy = computed(() => props.switching || props.lifecycleBusy !== null)
const busyStatus = computed(() =>
  props.lifecycleBusy === 'create' ? '正在新建 session…' : '正在切换 session…',
)

function itemSummary(item: SessionHistoryItem): string {
  const text = safeDisplayText(item.first_user_message.text, 240).trim()
  return text.length > 0 ? text : '（无首条用户消息）'
}

function itemTimestamp(item: SessionHistoryItem): string {
  return item.updated_at ?? item.created_at ?? '时间未知'
}

function itemState(item: SessionHistoryItem): string {
  return item.last_state ?? '状态未知'
}

function resumableLabel(item: SessionHistoryItem): string {
  return item.resumable ? '可恢复' : '不可恢复'
}

function diagnosticCodes(item: SessionHistoryItem): string {
  return item.diagnostics.map((diagnostic) => diagnostic.code).join(' · ')
}

function isActive(item: SessionHistoryItem): boolean {
  return props.activeSessionId === item.session_id
}

function canSelect(item: SessionHistoryItem): boolean {
  return item.resumable && !busy.value
}

function createSession(): void {
  if (busy.value) {
    return
  }
  emit('create')
}

function selectItem(item: SessionHistoryItem): void {
  if (!canSelect(item)) {
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
        <span aria-hidden="true">+</span>
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
      v-if="items.length > 0"
      class="history-sidebar__list"
      role="list"
    >
      <li
        v-for="item in items"
        :key="item.session_id"
        class="history-sidebar__item"
        :class="{
          'history-sidebar__item--current': isActive(item),
          'history-sidebar__item--blocked': !item.resumable,
        }"
        role="listitem"
      >
        <button
          class="history-sidebar__select"
          type="button"
          :disabled="!canSelect(item)"
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
            <span class="history-sidebar__state">{{ itemState(item) }}</span>
            <span
              class="history-sidebar__resume"
              :class="{ 'history-sidebar__resume--blocked': !item.resumable }"
            >{{ resumableLabel(item) }}</span>
          </span>
          <span
            v-if="item.first_user_message.truncated"
            class="history-sidebar__note"
          >摘要已截断</span>
          <span
            v-if="item.diagnostics.length > 0"
            class="history-sidebar__diagnostics"
          >{{ diagnosticCodes(item) }}</span>
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
