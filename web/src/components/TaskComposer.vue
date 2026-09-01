<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  readonly disabled?: boolean
  readonly pending?: boolean
}>(), {
  disabled: false,
  pending: false,
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
    aria-label="任务输入"
    :aria-busy="pending"
    @submit.prevent="submit"
  >
    <label
      class="sr-only"
      for="task-input"
    >
      任务内容
    </label>
    <div class="composer__field">
      <textarea
        id="task-input"
        v-model="text"
        class="composer__input"
        rows="4"
        :disabled="disabled || pending"
        placeholder="告诉 Agent 你想完成什么…"
        @keydown.ctrl.enter.prevent="submit"
        @keydown.meta.enter.prevent="submit"
      />
      <button
        class="composer__send"
        type="submit"
        :disabled="!canSubmit"
        aria-label="发送任务"
        title="发送任务"
      >
        <span aria-hidden="true">↑</span>
      </button>
    </div>
  </form>
</template>
