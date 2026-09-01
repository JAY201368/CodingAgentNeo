<script setup lang="ts">
import { computed } from 'vue'

import { safeDisplayText } from '../domain/events'
import type { ToolLifecycle } from '../domain/tools'
import BoundedText from './BoundedText.vue'

const props = defineProps<{
  readonly item: ToolLifecycle
}>()

const statusLabels: Record<string, string> = {
  success: '成功',
  error: '失败',
  denied: '已拒绝',
  invalid: '无效',
  cancelled: '已取消',
  timeout: '超时',
}

const toolName = computed(() => safeDisplayText(props.item.toolName, 200))
const correlationId = computed(() => safeDisplayText(props.item.correlationId, 300))
const resultStatus = computed(() => {
  if (!props.item.resultReceived) {
    return '等待结果'
  }
  if (props.item.resultStatus === null) {
    return '状态不可用'
  }
  return `${statusLabels[props.item.resultStatus] ?? '未知'}（${props.item.resultStatus}）`
})
const duration = computed(() => {
  if (props.item.durationSeconds === null) {
    return '耗时不可用'
  }
  return `${props.item.durationSeconds.toFixed(3)} 秒`
})
const approvalLabel = computed(() => {
  if (props.item.approvalRequest === null) {
    return '未请求授权'
  }
  if (!props.item.approvalCorrelationValid) {
    return '授权请求无效（未发送授权）'
  }
  return '已记录授权请求'
})
</script>

<template>
  <article
    class="tool-card"
    :data-correlation-id="correlationId"
    :aria-label="`工具生命周期：${toolName}`"
  >
    <div class="tool-card__header">
      <div>
        <p class="tool-card__eyebrow">
          工具生命周期
        </p>
        <h3 class="tool-card__title">
          {{ toolName }}
        </h3>
      </div>
      <span class="tool-card__status">
        {{ resultStatus }}
      </span>
    </div>

    <dl class="tool-card__facts">
      <div>
        <dt>关联 ID</dt>
        <dd>{{ correlationId }}</dd>
      </div>
      <div>
        <dt>授权</dt>
        <dd>{{ approvalLabel }}</dd>
      </div>
      <div>
        <dt>耗时</dt>
        <dd>{{ duration }}</dd>
      </div>
      <div>
        <dt>退出码</dt>
        <dd>{{ item.exitCode === null ? '不可用' : item.exitCode }}</dd>
      </div>
      <div v-if="item.timedOut">
        <dt>超时</dt>
        <dd>是</dd>
      </div>
    </dl>

    <section
      v-if="item.approvalSummary !== null"
      class="tool-card__section"
      aria-label="后端脱敏摘要"
    >
      <h4>
        后端脱敏摘要
      </h4>
      <BoundedText
        :value="item.approvalSummary"
        label="授权摘要"
      />
    </section>

    <p
      v-if="item.policyDecisionText !== null"
      class="tool-card__fact"
    >
      策略结果：{{ item.policyDecisionText }}
    </p>

    <section
      v-if="item.resultText !== null"
      class="tool-card__section"
      aria-label="工具结果"
    >
      <h4>
        工具结果
      </h4>
      <BoundedText
        :value="item.resultText"
        label="工具结果"
      />
    </section>

    <p
      v-if="item.truncated"
      class="tool-card__note"
      role="note"
    >
      工具结果已截断，仅展示后端提供的安全预览。
    </p>
  </article>
</template>
