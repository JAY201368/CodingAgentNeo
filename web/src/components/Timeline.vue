<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import BoundedText from './BoundedText.vue'
import type { TimelineItem } from '../domain/timeline'

const props = defineProps<{
  readonly items: readonly TimelineItem[]
}>()

interface TimelineTurn {
  readonly key: string
  readonly user: TimelineItem | null
  readonly process: readonly TimelineItem[]
  readonly final: TimelineItem | null
}

const rootEl = ref<HTMLElement | null>(null)
const expandedTurns = ref<ReadonlySet<string>>(new Set())

function nearestScrollContainer(start: HTMLElement | null): HTMLElement {
  let node: HTMLElement | null = start
  while (node !== null) {
    const style = globalThis.getComputedStyle(node)
    if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
      return node
    }
    node = node.parentElement
  }
  return (globalThis.document.scrollingElement ?? globalThis.document.documentElement) as HTMLElement
}
const turns = computed<readonly TimelineTurn[]>(() => {
  const result: TimelineTurn[] = []
  let prelude: TimelineItem[] = []
  let current: {
    user: TimelineItem | null
    process: TimelineItem[]
    final: TimelineItem | null
  } | null = null

  const appendCurrent = (): void => {
    if (current === null) {
      return
    }
    const first = current.user ?? current.process[0] ?? current.final
    result.push({
      key: first === null ? `turn-${result.length}` : `turn-${first.sequence}`,
      user: current.user,
      process: current.process,
      final: current.final,
    })
  }

  for (const item of props.items) {
    if (item.event.type === 'user_message') {
      appendCurrent()
      current = { user: item, process: prelude, final: null }
      prelude = []
    } else if (item.event.type === 'turn_end') {
      if (current === null) {
        current = { user: null, process: prelude, final: item }
        prelude = []
      } else {
        current.final = item
      }
    } else if (current === null) {
      prelude.push(item)
    } else {
      current.process.push(item)
    }
  }

  appendCurrent()
  if (prelude.length > 0) {
    result.push({
      key: `turn-${prelude[0].sequence}`,
      user: null,
      process: prelude,
      final: null,
    })
  }
  return result
})

function isExpanded(key: string): boolean {
  return expandedTurns.value.has(key)
}

function toggleTurn(key: string): void {
  const next = new Set(expandedTurns.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expandedTurns.value = next
}

function pageIsNearBottom(): boolean {
  const root = nearestScrollContainer(rootEl.value)
  return root.scrollHeight - root.scrollTop - root.clientHeight <= 160
}

watch(() => props.items.length, async (length, previousLength) => {
  if (length <= previousLength) {
    return
  }
  const shouldFollow = previousLength === 0 || pageIsNearBottom()
  await nextTick()
  if (shouldFollow) {
    const root = nearestScrollContainer(rootEl.value)
    root.scrollTop = root.scrollHeight
  }
})
</script>

<template>
  <section
    ref="rootEl"
    class="timeline"
    aria-label="消息区域"
  >
    <p
      v-if="items.length === 0"
      class="timeline__empty"
    >
      还没有事件。提交任务后，Agent 的事实会显示在这里。
    </p>

    <div
      v-else
      class="timeline__turns"
      aria-label="Agent 消息列表"
      aria-live="polite"
      aria-atomic="false"
    >
      <article
        v-for="turn in turns"
        :key="turn.key"
        class="timeline__turn"
      >
        <ol
          v-if="turn.user"
          class="timeline__list timeline__list--user"
          aria-label="用户消息"
        >
          <li class="timeline__item timeline__item--user">
            <div
              v-if="isExpanded(turn.key)"
              class="timeline__meta"
            >
              <span class="timeline__title">{{ turn.user.title }}</span>
            </div>
            <BoundedText
              :value="turn.user.text"
              :label="turn.user.title"
            />
            <p
              v-if="turn.user.truncated"
              class="timeline__note"
              role="note"
            >
              此事件 payload 已截断，页面只展示安全预览。
            </p>
          </li>
        </ol>

        <button
          v-if="turn.process.length > 0"
          class="timeline__process-toggle"
          type="button"
          :aria-expanded="isExpanded(turn.key)"
          @click="toggleTurn(turn.key)"
        >
          <span>{{ isExpanded(turn.key) ? '收起思考过程' : '展开思考过程' }}</span>
          <span aria-hidden="true">{{ isExpanded(turn.key) ? '−' : '+' }}</span>
        </button>

        <ol
          v-if="isExpanded(turn.key) && turn.process.length > 0"
          class="timeline__list timeline__list--process"
          aria-label="Turn 详细过程"
        >
          <li
            v-for="item in turn.process"
            :key="`${item.sequence}-${item.event.eventId}`"
            class="timeline__item"
            :class="`timeline__item--${item.kind}`"
          >
            <div class="timeline__meta">
              <span class="timeline__title">{{ item.title }}</span>
            </div>
            <BoundedText
              :value="item.text"
              :label="item.title"
            />
            <p
              v-if="item.truncated"
              class="timeline__note"
              role="note"
            >
              此事件 payload 已截断，页面只展示安全预览。
            </p>
          </li>
        </ol>

        <ol
          v-if="turn.final"
          class="timeline__list timeline__list--final"
          aria-label="Turn 最终回复"
        >
          <li
            class="timeline__item timeline__item--end"
          >
            <div class="timeline__meta">
              <span class="timeline__title">{{ turn.final.title }}</span>
            </div>
            <BoundedText
              :value="turn.final.text"
              :label="turn.final.title"
            />
            <p
              v-if="turn.final.truncated"
              class="timeline__note"
              role="note"
            >
              此事件 payload 已截断，页面只展示安全预览。
            </p>
          </li>
        </ol>
      </article>
    </div>
  </section>
</template>
