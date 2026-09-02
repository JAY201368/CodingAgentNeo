<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ApprovalMode } from '../domain/protocol'

const props = withDefaults(defineProps<{
  readonly disabled?: boolean
  readonly pending?: boolean
  readonly approvalMode?: ApprovalMode
  readonly permissionsUpdating?: boolean
}>(), {
  disabled: false,
  pending: false,
  approvalMode: 'ask',
  permissionsUpdating: false,
})

const emit = defineEmits<{
  submit: [text: string]
  permissionChange: [mode: ApprovalMode]
}>()

const permissionOptions: ReadonlyArray<{ mode: ApprovalMode; label: string; title: string }> = [
  { mode: 'ask', label: '询问', title: '写入和命令每次询问' },
  { mode: 'auto', label: '自动', title: '自动允许写入和命令' },
  { mode: 'deny', label: '只读', title: '禁止写入和命令' },
]

const text = ref('')
const localSubmissionLock = ref(false)
const permissionsOpen = ref(false)
const permissionsMenu = ref<HTMLElement | null>(null)
const currentPermission = computed(() => (
  permissionOptions.find((option) => option.mode === props.approvalMode) ?? permissionOptions[0]
))
const canSubmit = computed(() =>
  !props.disabled && !props.pending && !localSubmissionLock.value && text.value.trim().length > 0,
)

watch(() => props.pending, (pending) => {
  if (!pending) {
    localSubmissionLock.value = false
  }
})

watch(() => props.permissionsUpdating, (updating) => {
  if (updating) {
    permissionsOpen.value = false
  }
})

function togglePermissions(): void {
  if (!props.permissionsUpdating) {
    permissionsOpen.value = !permissionsOpen.value
  }
}

function selectPermission(mode: ApprovalMode): void {
  permissionsOpen.value = false
  if (mode !== props.approvalMode) {
    emit('permissionChange', mode)
  }
}

function closePermissionsOnFocusOut(event: FocusEvent): void {
  const next = event.relatedTarget
  if (!(next instanceof HTMLElement) || !permissionsMenu.value?.contains(next)) {
    permissionsOpen.value = false
  }
}

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
        placeholder="Do Anything"
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
      <div
        ref="permissionsMenu"
        class="composer__permissions"
        :aria-busy="permissionsUpdating"
        @focusout="closePermissionsOnFocusOut"
        @keydown.esc.stop="permissionsOpen = false"
      >
        <button
          class="composer__permission-trigger"
          type="button"
          aria-haspopup="listbox"
          :aria-expanded="permissionsOpen"
          aria-controls="permission-options"
          :title="currentPermission.title"
          :disabled="permissionsUpdating"
          @click="togglePermissions"
        >
          <span>{{ currentPermission.label }}</span>
          <span
            class="composer__permission-chevron"
            :class="{ 'composer__permission-chevron--open': permissionsOpen }"
            aria-hidden="true"
          >⌃</span>
        </button>
        <div
          v-if="permissionsOpen"
          id="permission-options"
          class="composer__permission-menu"
          role="listbox"
          aria-label="Agent 权限"
        >
          <button
            v-for="option in permissionOptions"
            :key="option.mode"
            class="composer__permission-option"
            :class="{ 'composer__permission-option--active': approvalMode === option.mode }"
            type="button"
            role="option"
            :aria-selected="approvalMode === option.mode"
            :title="option.title"
            @click="selectPermission(option.mode)"
          >
            <span>{{ option.label }}</span>
            <span
              v-if="approvalMode === option.mode"
              aria-hidden="true"
            >✓</span>
          </button>
        </div>
      </div>
    </div>
  </form>
</template>
