import { isRecord, type AgentEventEnvelope } from './protocol'
import { isTruncatedPayload, payloadString, safeDisplayText } from './events'

/**
 * A deliberately small, display-only projection of canonical events.
 *
 * Timeline items never turn payload values back into commands.  Values from
 * the transport are treated as untrusted text and are bounded before they
 * reach a component.
 */
export type TimelineItemKind = 'user' | 'assistant' | 'run' | 'error' | 'end'

export interface TimelineItem {
  readonly event: AgentEventEnvelope
  readonly sequence: number
  readonly kind: TimelineItemKind
  readonly title: string
  readonly text: string
  readonly truncated: boolean
}
const MAX_TIMELINE_TEXT = 20_000

function firstText(
  event: AgentEventEnvelope,
  fields: readonly string[],
): string | null {
  for (const field of fields) {
    const value = payloadString(event.payload, field)
    if (value !== null) {
      return safeDisplayText(value, MAX_TIMELINE_TEXT)
    }
  }
  return null
}

function stateText(event: AgentEventEnvelope): string {
  const state = payloadString(event.payload, 'state')
  const reason = firstText(event, ['reason', 'limit_reason', 'error_type'])
  if (state !== null && reason !== null) {
    return `${state} · ${reason}`
  }
  return state ?? reason ?? '状态信息不可用'
}

function truncatedText(event: AgentEventEnvelope): string | null {
  if (!isTruncatedPayload(event.payload)) {
    return null
  }
  const head = safeDisplayText(event.payload.head, 2_000)
  const tail = safeDisplayText(event.payload.tail, 2_000)
  if (head !== '[untrusted payload]' || tail !== '[untrusted payload]') {
    const preview = [head, tail]
      .filter((part) => part.length > 0 && part !== '[untrusted payload]')
      .join(' … ')
    if (preview.length > 0) {
      return `内容已截断：${preview}`
    }
  }
  return '内容已截断，当前仅显示安全预览。'
}

function toolName(event: AgentEventEnvelope): string {
  return firstText(event, ['tool_name', 'name']) ?? '未知工具'
}

function toolResultText(event: AgentEventEnvelope): string {
  const nested = isRecord(event.payload.result)
    ? event.payload.result
    : isRecord(event.payload.tool_result)
      ? event.payload.tool_result
      : event.payload
  const status = payloadString(nested, 'status') ?? '结果状态未知'
  const rawText = payloadString(nested, 'text')
  const text = rawText === null ? null : safeDisplayText(rawText, MAX_TIMELINE_TEXT)
  return text === null ? `工具结果：${status}` : `工具结果：${status} · ${text}`
}

function approvalCorrelationValid(event: AgentEventEnvelope): boolean {
  const requestId = payloadString(event.payload, 'request_id')
  return requestId !== null && event.correlationId !== null && requestId === event.correlationId
}

function itemFor(event: AgentEventEnvelope): TimelineItem {
  const truncated = isTruncatedPayload(event.payload)
  const preview = truncatedText(event)
  let kind: TimelineItemKind = 'run'
  let title = '运行事件'
  let text = preview ?? '事件内容不可用'

  switch (event.type) {
    case 'user_message':
      kind = 'user'
      title = '用户任务'
      text = preview ?? firstText(event, ['text']) ?? '用户消息不可用'
      break
    case 'assistant_message':
      kind = 'assistant'
      title = 'Assistant 回复'
      text = preview ?? firstText(event, ['text']) ?? 'Assistant 文本不可用'
      break
    case 'turn_end':
      kind = 'end'
      title = 'Turn 结束'
      text = preview ?? firstText(event, ['assistant_text']) ?? stateText(event)
      break
    case 'error':
      kind = 'error'
      title = '执行错误'
      text = preview ?? firstText(event, ['message', 'reason', 'error_type']) ?? '发生了未提供详情的错误'
      break
    case 'agent_end':
      kind = 'end'
      title = 'Agent 结束'
      text = preview ?? stateText(event)
      break
    case 'session_end':
      kind = 'end'
      title = 'Session 结束'
      text = preview ?? stateText(event)
      break
    case 'session_start':
      title = 'Session 开始'
      text = preview ?? stateText(event)
      break
    case 'agent_start':
      title = 'Agent 开始'
      text = preview ?? stateText(event)
      break
    case 'tool_call':
      title = '工具调用'
      text = preview ?? `工具：${toolName(event)}`
      break
    case 'approval_request':
      if (!approvalCorrelationValid(event)) {
        kind = 'error'
        title = '无效授权请求'
        text = '授权请求与事件关联 ID 不匹配，未发送批准或拒绝；可使用 Stop 中断。'
      } else {
        title = '等待授权'
        text = preview ?? `工具：${toolName(event)}（等待唯一授权响应）`
      }
      break
    case 'policy_decision':
      title = '策略决定'
      text = preview ?? firstText(event, ['decision', 'action', 'reason']) ?? '策略决定不可用'
      break
    case 'tool_result':
      title = '工具结果'
      text = preview ?? toolResultText(event)
      break
    case 'retry':
      title = '可恢复重试'
      text = preview ?? firstText(event, ['reason', 'category']) ?? '重试原因不可用'
      break
    case 'compaction':
      title = '上下文整理'
      text = preview ?? firstText(event, ['status', 'reason', 'summary']) ?? '整理状态不可用'
      break
    default:
      title = `未知事件：${safeDisplayText(event.type, 200)}`
      text = preview ?? '未知事件已安全保留，未解释其 payload。'
      break
  }

  return { event, sequence: event.sequence, kind, title, text, truncated }
}

/** Return a stable sequence-ordered projection for the timeline component. */
export function projectTimeline(events: readonly AgentEventEnvelope[]): readonly TimelineItem[] {
  return [...events]
    .sort((left, right) => left.sequence - right.sequence)
    .map(itemFor)
}
