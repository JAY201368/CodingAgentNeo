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
  readonly autoConnect?: boolean
}>(), {
  client: undefined,
  storage: undefined,
  autoConnect: true,
})

const sharedClient = props.client ?? new AgentHttpClient()
const session = useAgentSession({
  client: sharedClient,
  storage: props.storage,
})
const history = useSessionHistory({
  client: sharedClient,
})
const composer = ref<InstanceType<typeof TaskComposer> | null>(null)
const sessionEntry = ref<globalThis.HTMLElement | null>(null)
const actionError = ref<string | null>(null)
const connecting = ref(false)
const endingSession = ref(false)
const selectedHistorySessionId = ref<string | null>(null)
let connectTimer: ReturnType<typeof globalThis.setTimeout> | null = null

const timelineItems = computed(() => projectTimeline(session.state.value.events))
const pendingApproval = computed(() => session.state.value.pendingApproval)
const isConnected = computed(() =>
  session.state.value.connection === 'connected' && session.transportSessionId.value !== null,
)
const showWorkspace = computed(() =>
  session.transportSessionId.value !== null &&
  (session.state.value.connection === 'connected' || session.state.value.connection === 'closed'),
)
const canRetryConnection = computed(() =>
  session.state.value.connection === 'error' || session.state.value.connection === 'closed',
)
const hasResumeHint = computed(() => session.storedSession.value !== null)
const streamRetryExhausted = computed(() => session.state.value.streamRetryExhausted === true)
const showStreamRetry = computed(() =>
  isConnected.value && !session.state.value.streamAvailable && streamRetryExhausted.value,
)
const showEndSession = computed(() =>
  isConnected.value && session.gate.value.canClose,
)
const hasDiagnostics = computed(() => session.state.value.diagnostics.length > 0)
const displayError = computed(() => actionError.value ?? session.state.value.lastError)
const approvalSubmitting = computed(() => session.state.value.commandInFlight === 'ApprovalResponse')
const composerDisabled = computed(() =>
  !session.gate.value.canSubmitTask || session.switching.value,
)

type ErrorContext = 'resume'

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
          return '当前 session 已结束，请新建一个本地 session。'
        }
        return '已有一个活动 session。请关闭其他页面后再重试。'
      case 'turn_in_progress':
        return '当前 turn 正在运行，请等待结束后再提交。'
      case 'session_not_found':
      case 'session_closed':
        return 'Agent session 已关闭或不存在，请新建一个本地 session。'
      case 'history_not_found':
        return '找不到该历史 session。当前 session 已结束，请新建一个本地 session。'
      case 'history_unavailable':
        return '该历史 session 暂时无法恢复。当前 session 已结束，请新建一个本地 session。'
      case 'invalid_resume':
        return '该历史 session 无法恢复。当前 session 已结束，请新建一个本地 session。'
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

async function connect(): Promise<void> {
  if (connecting.value) {
    return
  }
  connecting.value = true
  actionError.value = null
  try {
    await session.connect()
  } catch (error) {
    handleFailure(error)
  } finally {
    connecting.value = false
  }
}

function retryConnection(): void {
  if (connecting.value) {
    return
  }
  // An existing storage hint must be queried again before any new session is
  // created.  Only a missing/closed hint takes the explicit new-session path.
  session.stopEvents()
  if (
    session.state.value.connection === 'connected' &&
    session.transportSessionId.value !== null
  ) {
    session.startEvents()
    return
  }
  if (session.storedSession.value === null) {
    session.dispatch({ type: 'RESET' })
    selectedHistorySessionId.value = null
  }
  void connect()
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

async function endSession(): Promise<void> {
  if (endingSession.value) {
    return
  }
  endingSession.value = true
  actionError.value = null
  try {
    // DELETE is the explicit transport lifecycle operation.  It is never
    // attached to component teardown, so browser teardown is not close evidence.
    await session.deleteSession()
    selectedHistorySessionId.value = null
    await nextTick()
    if (typeof sessionEntry.value?.scrollIntoView === 'function') {
      sessionEntry.value.scrollIntoView({ behavior: 'smooth', block: 'end' })
    } else {
      const root = globalThis.document.scrollingElement ?? globalThis.document.documentElement
      root.scrollTop = root.scrollHeight
    }
  } catch (error) {
    handleFailure(error)
  } finally {
    endingSession.value = false
  }
}

function onSelectHistory(historySessionId: string): void {
  void selectHistorySession(historySessionId)
}

async function selectHistorySession(historySessionId: string): Promise<void> {
  if (session.switching.value) {
    return
  }
  if (needsSwitchConfirmation()) {
    const confirmed = globalThis.confirm('将终结当前正在进行的工作并切换 session')
    if (!confirmed) {
      return
    }
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
  if (!props.autoConnect) {
    return
  }
  // Defer the initial network effect by one macrotask so the disconnected
  // state is rendered first and a slow/unavailable server cannot block mount.
  connectTimer = globalThis.setTimeout(() => {
    connectTimer = null
    void connect()
  }, 0)
})

onBeforeUnmount(() => {
  if (connectTimer !== null) {
    globalThis.clearTimeout(connectTimer)
    connectTimer = null
  }
  session.stopEvents()
})
</script>

<template>
  <div class="app-layout">
    <HistorySidebar
      :items="history.items.value"
      :loading="history.loading.value"
      :error="history.error.value"
      :has-more="history.hasMore.value"
      :active-session-id="selectedHistorySessionId"
      :switching="session.switching.value"
      @select="onSelectHistory"
      @load-more="onLoadMore"
      @refresh="onRefresh"
    >
      <template #title>
        <p class="eyebrow">
          CodingAgentNeo
        </p>
        <h1 id="app-title">
          CodingAgentNeo Web
        </h1>
      </template>
    </HistorySidebar>

    <div class="app-main">
      <main
        class="app-shell"
        aria-labelledby="app-title"
      >
        <header
          v-if="showEndSession"
          class="app-header"
        >
          <div class="app-header__actions">
            <button
              class="secondary-action app-header__end-session"
              type="button"
              :disabled="endingSession || session.switching.value"
              @click="endSession"
            >
              {{ endingSession ? '正在结束…' : '结束 Session' }}
            </button>
          </div>
        </header>

        <template v-if="showWorkspace">
          <div class="conversation-workspace">
            <Timeline :items="timelineItems" />

            <TaskComposer
              ref="composer"
              :disabled="composerDisabled"
              :pending="session.state.value.commandInFlight === 'SubmitTask'"
              @submit="submitTask"
            />
          </div>
        </template>

        <div
          v-if="displayError || showStreamRetry || pendingApproval !== null || hasDiagnostics || canRetryConnection"
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

          <section
            v-if="showStreamRetry"
            class="connection-card"
            aria-labelledby="stream-retry-title"
          >
            <h2 id="stream-retry-title">
              事件流需要重新连接
            </h2>
            <p>
              自动重连已达到有限次数；session 仍保持存活，页面没有自动重放任何命令。
            </p>
            <button
              class="secondary-action"
              type="button"
              @click="retryConnection"
            >
              重新连接事件流
            </button>
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

          <section
            v-if="canRetryConnection"
            ref="sessionEntry"
            class="connection-card connection-card--session-entry"
            aria-label="Session 连接入口"
          >
            <p class="connection-card__message">
              {{ hasResumeHint ? '当前 Session 连接已中断' : '当前 Session 已结束' }}
            </p>
            <button
              class="secondary-action"
              type="button"
              :disabled="connecting"
              @click="retryConnection"
            >
              {{ connecting ? '创建中…' : (hasResumeHint ? '重新连接' : '新建 session') }}
            </button>
          </section>
        </div>

        <p
          v-if="!showWorkspace && !canRetryConnection && !connecting"
          class="loading-note"
          role="status"
          aria-live="polite"
        >
          <span
            class="loading-mark"
            aria-hidden="true"
          >○</span>
          <span>尚未连接 Agent 服务</span>
        </p>
        <p
          v-else-if="!showWorkspace && connecting"
          class="loading-note"
          role="status"
          aria-live="polite"
          aria-busy="true"
        >
          <span
            class="loading-mark"
            aria-hidden="true"
          >◌</span>
          <span>正在连接 Agent 服务…</span>
        </p>
      </main>
    </div>
  </div>
</template>
