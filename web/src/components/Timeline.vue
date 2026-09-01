<script setup lang="ts">
import BoundedText from './BoundedText.vue'
import type { TimelineItem } from '../domain/timeline'

defineProps<{
  readonly items: readonly TimelineItem[]
}>()
</script>

<template>
  <section
    class="timeline"
    aria-labelledby="timeline-title"
  >
    <div class="section-heading">
      <h2 id="timeline-title">
        事件时间线
      </h2>
      <span class="section-heading__hint">按 sequence 排列</span>
    </div>

    <p
      v-if="items.length === 0"
      class="timeline__empty"
    >
      还没有事件。提交任务后，Agent 的事实会显示在这里。
    </p>

    <ol
      v-else
      class="timeline__list"
      aria-label="Agent 事件列表"
      aria-live="polite"
      aria-atomic="false"
    >
      <li
        v-for="item in items"
        :key="`${item.sequence}-${item.event.eventId}`"
        class="timeline__item"
        :class="`timeline__item--${item.kind}`"
      >
        <div class="timeline__meta">
          <span class="timeline__sequence">#{{ item.sequence }}</span>
          <span class="timeline__title">{{ item.title }}</span>
          <span class="timeline__type">{{ item.event.type }}</span>
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
  </section>
</template>
