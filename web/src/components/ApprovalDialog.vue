<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'

import { safeDisplayText } from '../domain/events'
import type { PendingApproval } from '../domain/protocol'
import BoundedText from './BoundedText.vue'

const props = withDefaults(defineProps<{
  readonly approval: PendingApproval | null
  readonly disabled?: boolean
  readonly submitting?: boolean
  readonly streamAvailable?: boolean
}>(), {
  disabled: false,
  submitting: false,
  streamAvailable: true,
})

const emit = defineEmits<{
  decide: [requestId: string, approved: boolean]
  dismiss: []
}>()

const approveButton = ref<HTMLButtonElement | null>(null)
const reopenButton = ref<HTMLButtonElement | null>(null)
const dialogElement = ref<HTMLElement | null>(null)
const open = ref(false)
const submitted = ref(false)
let lastFocusedElement: HTMLElement | null = null

function approvalKey(approval: PendingApproval | null): string {
  return approval === null ? '' : `${approval.requestId}\u0000${approval.correlationId}`
}

function focusApproval(): void {
  if (open.value) {
    void nextTick(() => approveButton.value?.focus())
  }
}

function rememberFocus(): void {
  if (typeof document !== 'undefined' && document.activeElement instanceof HTMLElement) {
    lastFocusedElement = document.activeElement
  }
}

function restoreFocus(): void {
  const target = lastFocusedElement
  lastFocusedElement = null
  if (
    target === null ||
    !target.isConnected ||
    dialogElement.value?.contains(target) === true
  ) {
    return
  }
  void nextTick(() => target.focus())
}

function focusableElements(): HTMLElement[] {
  const dialog = dialogElement.value
  if (dialog === null) {
    return []
  }
  return Array.from(dialog.querySelectorAll<HTMLElement>(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )).filter((element) => !element.hasAttribute('hidden') && element.getAttribute('aria-hidden') !== 'true')
}

function handleDialogKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' || event.key === 'Esc' || event.key.toLowerCase() === 'esc') {
    event.preventDefault()
    closeWithoutDecision()
    return
  }
  if (event.key !== 'Tab') {
    return
  }
  const focusables = focusableElements()
  if (focusables.length === 0) {
    event.preventDefault()
    dialogElement.value?.focus()
    return
  }
  const activeElement = typeof document === 'undefined' ? null : document.activeElement
  const currentIndex = activeElement === null
    ? -1
    : focusables.indexOf(activeElement as HTMLElement)
  if (event.shiftKey && (currentIndex <= 0 || activeElement === dialogElement.value)) {
    event.preventDefault()
    focusables[focusables.length - 1]?.focus()
  } else if (!event.shiftKey && (currentIndex === focusables.length - 1 || currentIndex < 0)) {
    event.preventDefault()
    focusables[0]?.focus()
  }
}

function handleDialogFocusout(event: FocusEvent): void {
  const dialog = dialogElement.value
  const nextElement = event.relatedTarget instanceof HTMLElement ? event.relatedTarget : null
  if (!open.value || dialog === null || (nextElement !== null && dialog.contains(nextElement))) {
    return
  }
  // Keep the modal keyboard scope intact when focus leaves through a pointer
  // or an assistive-technology command instead of a trapped Tab key.
  void nextTick(() => {
    if (open.value) {
      approveButton.value?.focus()
    }
  })
}

watch(() => approvalKey(props.approval), (key) => {
  if (key.length === 0) {
    open.value = false
    submitted.value = false
    restoreFocus()
    return
  }
  rememberFocus()
  open.value = true
  submitted.value = false
  focusApproval()
}, { immediate: true })

onMounted(() => {
  // An immediate watcher runs before template refs exist on first mount.
  // Repeat the initial focus once the dialog buttons are in the DOM.
  if (props.approval !== null) {
    focusApproval()
  }
})

function closeWithoutDecision(): void {
  // Escape, backdrop clicks, unmount, and stream loss never emit a decision.
  open.value = false
  emit('dismiss')
  void nextTick(() => reopenButton.value?.focus())
}

function reopen(): void {
  if (props.approval === null) {
    return
  }
  open.value = true
  focusApproval()
}

function decide(approved: boolean): void {
  const approval = props.approval
  if (
    approval === null ||
    props.disabled ||
    props.submitting ||
    submitted.value ||
    typeof approval.requestId !== 'string' ||
    approval.requestId.trim().length === 0
  ) {
    return
  }
  // Lock before emitting so rapid clicks cannot enqueue two responses, even
  // before the parent reducer observes COMMAND_STARTED.
  submitted.value = true
  emit('decide', approval.requestId, approved)
}
</script>

<template>
  <section
    v-if="approval !== null && !open"
    class="approval-collapsed"
    aria-live="polite"
  >
    <p>
      有一个授权请求仍在等待处理；未因 Escape 或关闭对话框而批准。
    </p>
    <button
      ref="reopenButton"
      class="secondary-action"
      type="button"
      @click="reopen"
    >
      打开授权对话框
    </button>
  </section>

  <div
    v-else-if="approval !== null"
    class="approval-backdrop"
    role="presentation"
    @click.self="closeWithoutDecision"
  >
    <section
      ref="dialogElement"
      class="approval-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="approval-dialog-title"
      aria-describedby="approval-dialog-description"
      :aria-busy="disabled || submitting || submitted"
      tabindex="-1"
      @keydown="handleDialogKeydown"
      @focusout="handleDialogFocusout"
    >
      <div class="section-heading">
        <h2 id="approval-dialog-title">
          需要授权
        </h2>
        <span class="section-heading__hint">仅此一个待处理请求</span>
      </div>
      <p id="approval-dialog-description">
        Agent 请求执行工具「{{ safeDisplayText(approval.toolName, 200) }}」。下面内容是后端脱敏摘要，仅作查看。
      </p>
      <BoundedText
        :value="approval.argumentsSummary"
        label="授权摘要"
      />
      <p class="approval-dialog__meta">
        请求 ID 已与事件关联校验；超时：{{ approval.timeoutSeconds === null ? '不可用' : `${approval.timeoutSeconds} 秒` }}。
      </p>
      <p
        v-if="!streamAvailable"
        class="approval-dialog__warning"
        role="alert"
      >
        事件流已断开，授权操作保持关闭；可使用 Stop 中断当前 turn。
      </p>
      <p
        v-else-if="submitted || submitting"
        class="approval-dialog__status"
        role="status"
        aria-live="polite"
      >
        决定已提交，等待匹配的 policy event；按钮已锁定。
      </p>
      <div class="approval-dialog__actions">
        <button
          ref="approveButton"
          class="primary-action"
          type="button"
          :disabled="disabled || submitting || submitted || !streamAvailable"
          @click="decide(true)"
        >
          批准
        </button>
        <button
          class="secondary-action approval-dialog__deny"
          type="button"
          :disabled="disabled || submitting || submitted || !streamAvailable"
          @click="decide(false)"
        >
          拒绝
        </button>
        <button
          class="approval-dialog__close"
          type="button"
          :disabled="submitted || submitting"
          @click="closeWithoutDecision"
        >
          稍后处理
        </button>
      </div>
    </section>
  </div>
</template>
