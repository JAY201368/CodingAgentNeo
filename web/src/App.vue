<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  AgentApiError,
  AgentNetworkError,
  AgentRequestAbortedError,
} from './api/client'
import { useAgentSession, SessionCommandError } from './composables/useAgentSession'
import BoundedText from './components/BoundedText.vue'
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

const session = useAgentSession({
  client: props.client,
  storage: props.storage,
})
const composer = ref<InstanceType<typeof TaskComposer> | null>(null)
const actionError = ref<string | null>(null)
const connecting = ref(false)
let connectTimer: ReturnType<typeof globalThis.setTimeout> | null = null

const timelineItems = computed(() => projectTimeline(session.state.value.events))
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
const connectionLabel = computed(() => connectionLabels[session.state.value.connection] ?? '连接状态未知')
const composerReason = computed(() => {
  const gate = session.gate.value
  if (gate.kind === 'completed_turn') {
    return '上一轮已完成，可以继续输入 follow-up。'
  }
  if (gate.kind === 'waiting_for_approval') {
    return 'Agent 正在等待授权；本页面暂不提供授权操作。'
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
        return 'Agent session 已关闭或不存在，可以重新连接。'
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
    session.stopEvents()
    session.dispatch({ type: 'CLOSED' })
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
  // A new transport session starts a new display projection.  This is only
  // offered after a failed/closed connection; terminal sessions remain locked.
  session.stopEvents()
  session.dispatch({ type: 'RESET' })
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
        还没有可用的 session
      </h2>
      <p>
        可以确认 Agent HTTP 服务已在本机运行后重新连接。页面不会重放此前未确认的 POST 命令。
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
