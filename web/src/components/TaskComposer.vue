<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  readonly disabled?: boolean
  readonly pending?: boolean
  readonly statusReason?: string
}>(), {
  disabled: false,
  pending: false,
  statusReason: '',
})

const emit = defineEmits<{
  submit: [text: string]
}>()

const text = ref('')
const localSubmissionLock = ref(false)
const canSubmit = computed(() =>
  !props.disabled && !props.pending && !localSubmissionLock.value && text.value.trim().length > 0,
)

watch(() => props.pending, (pending) => {
  if (!pending) {
    localSubmissionLock.value = false
  }
})

function submit(): void {
  if (!canSubmit.value) {
    return
  }
  localSubmissionLock.value = true
  emit('submit', text.value)
}

function clear(): void {
  text.value = ''
  localSubmissionLock.value = false
}

function unlock(): void {
  localSubmissionLock.value = false
}

defineExpose({ clear, unlock })
</script>

<template>
  <form
    class="composer"
    aria-labelledby="composer-title"
    @submit.prevent="submit"
  >
    <div class="section-heading">
      <h2 id="composer-title">
        新任务
      </h2>
      <span class="section-heading__hint">单 turn，按顺序执行</span>
    </div>
    <label
      class="sr-only"
      for="task-input"
    >
      任务内容
    </label>
    <textarea
      id="task-input"
      v-model="text"
      class="composer__input"
      rows="4"
      :disabled="disabled || pending"
      :aria-describedby="statusReason ? 'composer-help' : undefined"
      placeholder="告诉 Agent 你想完成什么…"
      @keydown.ctrl.enter.prevent="submit"
      @keydown.meta.enter.prevent="submit"
    />
    <div class="composer__footer">
      <p
        v-if="statusReason"
        id="composer-help"
        class="composer__reason"
        role="status"
      >
        {{ statusReason }}
      </p>
      <button
        class="primary-action"
        type="submit"
        :disabled="!canSubmit"
      >
        {{ pending ? '提交中…' : '发送任务' }}
      </button>
    </div>
  </form>
</template>
