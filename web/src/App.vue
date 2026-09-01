<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  AgentApiError,
  AgentNetworkError,
  AgentRequestAbortedError,
} from './api/client'
import { useAgentSession, SessionCommandError } from './composables/useAgentSession'
import BoundedText from './components/BoundedText.vue'
import ApprovalDialog from './components/ApprovalDialog.vue'
import TaskComposer from './components/TaskComposer.vue'
import Timeline from './components/Timeline.vue'
import ToolCard from './components/ToolCard.vue'
import { projectTimeline } from './domain/timeline'
import { projectToolLifecycles } from './domain/tools'

const props = withDefaults(defineProps<{
  readonly client?: import('./api/client').AgentHttpClient
  readonly storage?: import('./composables/useAgentSession').UseAgentSessionOptions['storage']
  readonly autoConnect?: boolean
}>(), {
  client: undefined,
  storage: undefined,
  autoConnect: true,
})

const session = useAgentSession({
  client: props.client,
  storage: props.storage,
})
const composer = ref<InstanceType<typeof TaskComposer> | null>(null)
const actionError = ref<string | null>(null)
const connecting = ref(false)
const endingSession = ref(false)
let connectTimer: ReturnType<typeof globalThis.setTimeout> | null = null

const timelineItems = computed(() => projectTimeline(session.state.value.events))
const toolLifecycles = computed(() => projectToolLifecycles(session.state.value.events))
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
  isConnected.value && session.gate.value.canClose && !endingSession.value,
)
const finalReply = computed(() =>
  session.state.value.finalAssistantText || session.state.value.latestAssistantText,
)
const finalReplySource = computed(() =>
  session.state.value.finalAssistantText ? '来自最近一次 turn_end' : '来自最近一次 assistant 回复',
)

const statusLabels: Record<string, string> = {
  RUNNING: '运行中',
  WAITING_FOR_APPROVAL: '等待授权',
  COMPLETED_TURN: '本轮已完成',
  LIMIT_REACHED: '已达到限制',
  INTERRUPTED: '已中断',
  FAILED: '执行失败',
}

const connectionLabels: Record<string, string> = {
  disconnected: '尚未连接 Agent 服务',
  connecting: '正在连接 Agent 服务…',
  connected: '已连接 Agent 服务',
  closed: 'Session 已关闭',
  error: 'Agent 服务暂时不可用',
}

const statusLabel = computed(() => statusLabels[session.state.value.status] ?? '状态未知')
const connectionLabel = computed(() => {
  if (isConnected.value && !session.state.value.streamAvailable) {
    return streamRetryExhausted.value ? '事件流已断开' : '事件流连接中…'
  }
  return connectionLabels[session.state.value.connection] ?? '连接状态未知'
})
const composerReason = computed(() => {
  const gate = session.gate.value
  if (gate.kind === 'completed_turn') {
    return '上一轮已完成，可以继续输入 follow-up。'
  }
  if (gate.kind === 'waiting_for_approval') {
    return gate.reason
  }
  if (gate.kind === 'terminal') {
    return `Session ${statusLabel.value}，不能继续提交任务。`
  }
  if (gate.kind === 'turn_running' || gate.kind === 'command_pending') {
    return gate.reason
  }
  if (!isConnected.value) {
    return '连接成功后可以提交任务。'
  }
  return ''
})
const hasDiagnostics = computed(() => session.state.value.diagnostics.length > 0)
const displayError = computed(() => actionError.value ?? session.state.value.lastError)
const approvalSubmitting = computed(() => session.state.value.commandInFlight === 'ApprovalResponse')
const interruptSubmitting = computed(() => session.state.value.commandInFlight === 'Interrupt')
const showStop = computed(() =>
  isConnected.value &&
  session.state.value.turnActive &&
  (session.state.value.status === 'RUNNING' || session.state.value.status === 'WAITING_FOR_APPROVAL'),
)

function safeErrorMessage(error: unknown): string {
  if (error instanceof AgentApiError) {
    switch (error.code) {
      case 'invalid_host':
      case 'invalid_origin':
        return 'Agent 服务拒绝了当前本地来源，请从允许的本地地址访问。'
      case 'invalid_session_request':
      case 'invalid_command':
        return '请求格式无效，请检查输入后重试。'
      case 'session_exists':
        return '已有一个活动 session。请关闭其他页面后再重试。'
      case 'turn_in_progress':
        return '当前 turn 正在运行，请等待结束后再提交。'
      case 'session_not_found':
      case 'session_closed':
        return 'Agent session 已关闭或不存在，请新建一个本地 session。'
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

function handleFailure(error: unknown): void {
  actionError.value = safeErrorMessage(error)
  if (error instanceof AgentApiError &&
      (error.code === 'session_closed' || error.code === 'session_not_found')) {
    session.forgetSession()
  }
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

async function stopTurn(): Promise<void> {
  actionError.value = null
  try {
    await session.interrupt()
  } catch (error) {
    handleFailure(error)
    if (session.state.value.commandUncertain) {
      actionError.value = `${safeErrorMessage(error)} 中断结果未知，页面不会自动重试。`
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
  } catch (error) {
    handleFailure(error)
  } finally {
    endingSession.value = false
  }
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
  <main class="app-shell">
    <header class="app-header">
      <div>
        <p class="eyebrow">
          CodingAgentNeo
        </p>
        <h1>
          CodingAgentNeo Web
        </h1>
        <p class="app-header__subtitle">
          用一个线性 session 完成任务，并保留每条运行事实。
        </p>
      </div>
      <div class="status-stack">
        <p
          class="connection-status"
          role="status"
          aria-live="polite"
        >
          {{ connectionLabel }}
        </p>
        <p
          v-if="isConnected"
          class="runtime-status"
          :data-state="session.state.value.status"
          role="status"
          aria-live="polite"
        >
          {{ statusLabel }}
        </p>
      </div>
    </header>

    <section
      v-if="displayError"
      class="alert"
      role="alert"
    >
      {{ displayError }}
    </section>

    <section
      v-if="canRetryConnection"
      class="connection-card"
      aria-labelledby="connection-title"
    >
      <h2 id="connection-title">
        {{ hasResumeHint ? '正在恢复已有 session' : '需要新建 session' }}
      </h2>
      <p>
        <template v-if="hasResumeHint">
          页面会先查询本地保存的 transport ID，再从最后成功游标重新订阅；不会声称恢复已重启进程中的历史 session。
        </template>
        <template v-else>
          可以确认 Agent HTTP 服务已在本机运行后创建一个新的 session。页面不会重放此前未确认的 POST 命令。
        </template>
      </p>
      <button
        class="secondary-action"
        type="button"
        :disabled="connecting"
        @click="retryConnection"
      >
        {{ connecting ? '连接中…' : '重新连接' }}
      </button>
    </section>

    <template v-if="showWorkspace">
      <TaskComposer
        ref="composer"
        :disabled="!session.gate.value.canSubmitTask"
        :pending="session.state.value.commandInFlight === 'SubmitTask'"
        :status-reason="composerReason"
        @submit="submitTask"
      />

      <section
        v-if="showStop"
        class="run-controls"
        aria-labelledby="run-controls-title"
      >
        <div>
          <h2 id="run-controls-title">
            当前 turn
          </h2>
          <p>
            {{ interruptSubmitting ? '中断请求已发送，等待 INTERRUPTED 结束事件。' : '任务正在运行；可主动停止。' }}
          </p>
        </div>
        <button
          class="stop-action"
          type="button"
          :disabled="!session.gate.value.canInterrupt || interruptSubmitting"
          @click="stopTurn"
        >
          {{ interruptSubmitting ? '正在停止…' : '停止（Stop）' }}
        </button>
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

      <section
        v-if="toolLifecycles.length > 0"
        class="tool-lifecycles"
        aria-labelledby="tool-lifecycles-title"
      >
        <div class="section-heading">
          <h2 id="tool-lifecycles-title">
            工具执行
          </h2>
          <span class="section-heading__hint">按 correlation ID 聚合</span>
        </div>
        <div class="tool-lifecycles__list">
          <ToolCard
            v-for="item in toolLifecycles"
            :key="item.correlationId"
            :item="item"
          />
        </div>
      </section>

      <section
        v-if="finalReply"
        class="final-reply"
        aria-labelledby="final-reply-title"
      >
        <div class="section-heading">
          <h2 id="final-reply-title">
            最终回复
          </h2>
          <span class="section-heading__hint">{{ finalReplySource }}</span>
        </div>
        <BoundedText
          :value="finalReply"
          label="最终回复"
        />
      </section>

      <Timeline :items="timelineItems" />

      <section
        v-if="showEndSession"
        class="session-controls"
        aria-labelledby="session-controls-title"
      >
        <div>
          <h2 id="session-controls-title">
            Session 生命周期
          </h2>
          <p>需要结束时显式关闭；浏览器离开页面不会自动发送关闭命令。</p>
        </div>
        <button
          class="secondary-action"
          type="button"
          :disabled="endingSession"
          @click="endSession"
        >
          {{ endingSession ? '正在结束…' : '结束 Session' }}
        </button>
      </section>

      <p
        v-if="hasDiagnostics"
        class="diagnostic-note"
        role="status"
        aria-live="polite"
      >
        部分事件字段未知或不可用，已按安全文本降级展示（{{ session.state.value.diagnostics.length }} 条诊断）。
      </p>
    </template>

    <p
      v-else-if="!canRetryConnection && !connecting"
      class="loading-note"
      role="status"
      aria-live="polite"
    >
      尚未连接 Agent 服务
    </p>
  </main>
</template>
