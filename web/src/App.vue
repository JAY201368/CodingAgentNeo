<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  AgentApiError,
  AgentHttpClient,
  AgentNetworkError,
  AgentRequestAbortedError,
} from './api/client'
import { useAgentSession, SessionCommandError } from './composables/useAgentSession'
import { useSessionHistory } from './composables/useSessionHistory'
import ApprovalDialog from './components/ApprovalDialog.vue'
import HistorySidebar from './components/HistorySidebar.vue'
import TaskComposer from './components/TaskComposer.vue'
import Timeline from './components/Timeline.vue'
import { projectTimeline } from './domain/timeline'

const props = withDefaults(defineProps<{
  readonly client?: import('./api/client').AgentHttpClient
  readonly storage?: import('./composables/useAgentSession').UseAgentSessionOptions['storage']
}>(), {
  client: undefined,
  storage: undefined,
})

const sharedClient = props.client ?? new AgentHttpClient()
const session = useAgentSession({
  client: sharedClient,
  storage: props.storage,
})
const history = useSessionHistory({
  client: sharedClient,
})
const HISTORY_SIDEBAR_ID = 'history-sidebar'
const NARROW_MEDIA_QUERY = '(max-width: 640px)'

const composer = ref<InstanceType<typeof TaskComposer> | null>(null)
const historyToggle = ref<globalThis.HTMLButtonElement | null>(null)
const actionError = ref<string | null>(null)
const selectedHistorySessionId = ref<string | null>(null)
const historyDrawerOpen = ref(false)
const isNarrowViewport = ref(false)
let unsubscribeNarrowMedia: (() => void) | null = null

function closeHistoryDrawer(): void {
  historyDrawerOpen.value = false
}

function toggleHistoryDrawer(): void {
  historyDrawerOpen.value = !historyDrawerOpen.value
}

function onNarrowMediaChange(event: { readonly matches: boolean }): void {
  isNarrowViewport.value = event.matches
  if (!event.matches) {
    historyDrawerOpen.value = false
  }
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape' || !historyDrawerOpen.value) {
    return
  }
  closeHistoryDrawer()
  void nextTick(() => {
    historyToggle.value?.focus()
  })
}

function subscribeNarrowMedia(): void {
  if (typeof globalThis.matchMedia !== 'function') {
    return
  }
  const media = globalThis.matchMedia(NARROW_MEDIA_QUERY)
  isNarrowViewport.value = media.matches
  if (typeof media.addEventListener === 'function') {
    media.addEventListener('change', onNarrowMediaChange)
    unsubscribeNarrowMedia = () => {
      media.removeEventListener('change', onNarrowMediaChange)
    }
    return
  }
  media.addListener(onNarrowMediaChange)
  unsubscribeNarrowMedia = () => {
    media.removeListener(onNarrowMediaChange)
  }
}

const timelineItems = computed(() => projectTimeline(session.state.value.events))
const pendingApproval = computed(() => session.state.value.pendingApproval)
const isConnected = computed(() =>
  session.state.value.connection === 'connected' && session.transportSessionId.value !== null,
)
const showWorkspace = computed(() => isConnected.value)
const hasDiagnostics = computed(() => session.state.value.diagnostics.length > 0)
const displayError = computed(() => actionError.value ?? session.state.value.lastError)
const approvalSubmitting = computed(() => session.state.value.commandInFlight === 'ApprovalResponse')
const composerDisabled = computed(() =>
  !session.gate.value.canSubmitTask || session.switching.value,
)
const showMessageTail = computed(() =>
  displayError.value !== null ||
  pendingApproval.value !== null ||
  hasDiagnostics.value,
)

type ErrorContext = 'resume'

function sidebarRecoveryMessage(prefix: string): string {
  return `${prefix}请从左侧侧边栏新建或选择历史 session。`
}

function safeErrorMessage(error: unknown, context?: ErrorContext): string {
  if (error instanceof AgentApiError) {
    switch (error.code) {
      case 'invalid_host':
      case 'invalid_origin':
        return 'Agent 服务拒绝了当前本地来源，请从允许的本地地址访问。'
      case 'invalid_session_request':
      case 'invalid_command':
        return '请求格式无效，请检查输入后重试。'
      case 'session_exists':
        if (context === 'resume') {
          return sidebarRecoveryMessage('当前 session 已结束，')
        }
        return '已有一个活动 session。请关闭其他页面后再重试。'
      case 'turn_in_progress':
        return '当前 turn 正在运行，请等待结束后再提交。'
      case 'session_not_found':
      case 'session_closed':
        return sidebarRecoveryMessage('Agent session 已关闭或不存在，')
      case 'history_not_found':
        return sidebarRecoveryMessage('找不到该历史 session。当前 session 已结束，')
      case 'history_unavailable':
        return sidebarRecoveryMessage('该历史 session 暂时无法恢复。当前 session 已结束，')
      case 'invalid_resume':
        return sidebarRecoveryMessage('该历史 session 无法恢复。当前 session 已结束，')
      case 'invalid_history_id':
        return '历史 session 标识无效，未改变当前 session。'
      case 'internal_error':
      case 'protocol_error':
        return 'Agent 服务暂时无法完成请求，请稍后重试。'
      default:
        return 'Agent 服务请求失败，请稍后重试。'
    }
  }
  if (error instanceof AgentNetworkError) {
    return '无法连接 Agent 服务，请确认本地 Agent HTTP 服务正在运行。'
  }
  if (error instanceof AgentRequestAbortedError) {
    return '请求已取消；未自动重试。'
  }
  if (error instanceof SessionCommandError) {
    return error.message
  }
  return 'Agent 请求失败，请稍后重试。'
}

function handleFailure(error: unknown, context?: ErrorContext): void {
  actionError.value = safeErrorMessage(error, context)
  if (error instanceof AgentApiError &&
      (error.code === 'session_closed' || error.code === 'session_not_found')) {
    session.forgetSession()
  }
}

function needsSwitchConfirmation(): boolean {
  const snapshot = session.state.value
  return (
    snapshot.commandInFlight === 'SubmitTask' ||
    snapshot.pendingApproval !== null ||
    snapshot.turnActive ||
    snapshot.status === 'WAITING_FOR_APPROVAL' ||
    session.gate.value.kind === 'turn_running'
  )
}

function confirmReplacementIfNeeded(): boolean {
  if (!needsSwitchConfirmation()) {
    return true
  }
  return globalThis.confirm('将终结当前正在进行的工作并切换 session')
}

async function submitTask(text: string): Promise<void> {
  actionError.value = null
  try {
    await session.submitTask(text)
    composer.value?.clear()
  } catch (error) {
    handleFailure(error)
    if (session.state.value.commandUncertain) {
      actionError.value = `${safeErrorMessage(error)} 命令结果未知，页面不会自动重试。`
    } else {
      composer.value?.unlock()
    }
  }
}

async function respondToApproval(requestId: string, approved: boolean): Promise<void> {
  actionError.value = null
  try {
    await session.respondToApproval(requestId, approved)
  } catch (error) {
    handleFailure(error)
    if (session.state.value.commandUncertain) {
      actionError.value = `${safeErrorMessage(error)} 授权结果未知，页面不会自动重试。`
    }
  }
}

function onCreateSession(): void {
  closeHistoryDrawer()
  void createSessionFromSidebar()
}

async function createSessionFromSidebar(): Promise<void> {
  if (session.switching.value) {
    return
  }
  if (!confirmReplacementIfNeeded()) {
    return
  }
  actionError.value = null
  try {
    await session.createNewSession()
    selectedHistorySessionId.value = null
    try {
      await history.refresh()
    } catch {
      // A list refresh failure must not undo a successful create.
    }
  } catch (error) {
    if (session.transportSessionId.value === null) {
      selectedHistorySessionId.value = null
    }
    handleFailure(error)
  }
}

function onSelectHistory(historySessionId: string): void {
  closeHistoryDrawer()
  void selectHistorySession(historySessionId)
}

async function selectHistorySession(historySessionId: string): Promise<void> {
  if (session.switching.value) {
    return
  }
  if (!confirmReplacementIfNeeded()) {
    return
  }
  actionError.value = null
  try {
    await session.resumeSession(historySessionId)
    selectedHistorySessionId.value = historySessionId
    try {
      await history.refresh()
    } catch {
      // A list refresh failure must not undo a successful resume.
    }
  } catch (error) {
    if (session.transportSessionId.value === null) {
      selectedHistorySessionId.value = null
    }
    handleFailure(error, 'resume')
  }
}

function onLoadMore(): void {
  void history.loadMore()
}

function onRefresh(): void {
  void history.refresh()
}

onMounted(() => {
  subscribeNarrowMedia()
  globalThis.document.addEventListener('keydown', onDocumentKeydown)
})

onBeforeUnmount(() => {
  unsubscribeNarrowMedia?.()
  unsubscribeNarrowMedia = null
  globalThis.document.removeEventListener('keydown', onDocumentKeydown)
  session.stopEvents()
})
</script>

<template>
  <div
    class="app-layout"
    :class="{ 'app-layout--drawer-open': isNarrowViewport && historyDrawerOpen }"
  >
    <div
      v-if="isNarrowViewport && historyDrawerOpen"
      class="history-drawer-backdrop"
      @click="closeHistoryDrawer"
    />

    <HistorySidebar
      :id="HISTORY_SIDEBAR_ID"
      :class="{ 'history-sidebar--open': historyDrawerOpen }"
      :inert="isNarrowViewport && !historyDrawerOpen ? true : undefined"
      :aria-hidden="isNarrowViewport && !historyDrawerOpen ? true : undefined"
      :items="history.items.value"
      :loading="history.loading.value"
      :error="history.error.value"
      :has-more="history.hasMore.value"
      :active-session-id="selectedHistorySessionId"
      :switching="session.switching.value"
      :lifecycle-busy="session.lifecycleBusy.value"
      @create="onCreateSession"
      @select="onSelectHistory"
      @load-more="onLoadMore"
      @refresh="onRefresh"
    >
      <template #title>
        <h1 id="app-title">
          CodingAgentNeo
        </h1>
      </template>
    </HistorySidebar>

    <div class="app-main">
      <button
        v-if="isNarrowViewport"
        ref="historyToggle"
        class="history-drawer-toggle"
        type="button"
        :aria-expanded="historyDrawerOpen"
        :aria-controls="HISTORY_SIDEBAR_ID"
        :aria-label="historyDrawerOpen ? '关闭历史' : '打开历史'"
        @click="toggleHistoryDrawer"
      >
        <span
          class="history-drawer-toggle__icon"
          aria-hidden="true"
        >
          <span />
          <span />
          <span />
        </span>
        <span>{{ historyDrawerOpen ? '关闭历史' : '历史' }}</span>
      </button>

      <main
        class="app-shell"
        aria-labelledby="app-title"
        :inert="isNarrowViewport && historyDrawerOpen ? true : undefined"
      >
        <template v-if="showWorkspace">
          <div class="conversation-workspace">
            <div class="conversation-workspace__scroll">
              <Timeline :items="timelineItems" />

              <div
                v-if="showMessageTail"
                class="message-tail"
              >
                <section
                  v-if="displayError"
                  class="alert"
                  role="alert"
                  aria-live="assertive"
                >
                  <span
                    class="alert__mark"
                    aria-hidden="true"
                  >!</span>
                  <span>{{ displayError }}</span>
                </section>

                <ApprovalDialog
                  :approval="pendingApproval"
                  :disabled="!session.gate.value.canRespondToApproval"
                  :submitting="approvalSubmitting"
                  :stream-available="session.state.value.streamAvailable"
                  @decide="respondToApproval"
                />

                <p
                  v-if="hasDiagnostics"
                  class="diagnostic-note"
                  role="status"
                  aria-live="polite"
                >
                  部分事件字段未知或不可用，已按安全文本降级展示（{{ session.state.value.diagnostics.length }} 条诊断）。
                </p>
              </div>
            </div>

            <TaskComposer
              ref="composer"
              :disabled="composerDisabled"
              :pending="session.state.value.commandInFlight === 'SubmitTask'"
              @submit="submitTask"
            />
          </div>
        </template>

        <div
          v-else-if="showMessageTail"
          class="message-tail"
        >
          <section
            v-if="displayError"
            class="alert"
            role="alert"
            aria-live="assertive"
          >
            <span
              class="alert__mark"
              aria-hidden="true"
            >!</span>
            <span>{{ displayError }}</span>
          </section>

          <ApprovalDialog
            :approval="pendingApproval"
            :disabled="!session.gate.value.canRespondToApproval"
            :submitting="approvalSubmitting"
            :stream-available="session.state.value.streamAvailable"
            @decide="respondToApproval"
          />

          <p
            v-if="hasDiagnostics"
            class="diagnostic-note"
            role="status"
            aria-live="polite"
          >
            部分事件字段未知或不可用，已按安全文本降级展示（{{ session.state.value.diagnostics.length }} 条诊断）。
          </p>
        </div>
      </main>
    </div>
  </div>
</template>
