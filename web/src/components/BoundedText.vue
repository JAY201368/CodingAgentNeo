<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { safeDisplayText } from '../domain/events'

const props = withDefaults(defineProps<{
  readonly value: unknown
  readonly limit?: number
  readonly expandedLimit?: number
  readonly label?: string
}>(), {
  limit: 900,
  expandedLimit: 20_000,
  label: '文本',
})

const expanded = ref(false)
const fullText = computed(() => safeDisplayText(props.value, props.expandedLimit))
const previewText = computed(() => safeDisplayText(props.value, props.limit))
const expandable = computed(() =>
  typeof props.value === 'string' && props.value.length > props.limit,
)
const displayText = computed(() => expanded.value ? fullText.value : previewText.value)

watch(() => props.value, () => {
  expanded.value = false
})

function toggleExpanded(): void {
  expanded.value = !expanded.value
}
</script>

<template>
  <div class="bounded-text">
    <p class="bounded-text__value">
      {{ displayText }}
    </p>
    <button
      v-if="expandable"
      class="bounded-text__toggle"
      type="button"
      :aria-expanded="expanded"
      :aria-label="`${expanded ? '收起' : '展开'}${label}`"
      @click="toggleExpanded"
    >
      {{ expanded ? '收起' : '展开' }}
    </button>
  </div>
</template>
