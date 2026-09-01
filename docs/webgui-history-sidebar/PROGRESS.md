# CodingAgentNeo Web GUI 历史侧边栏进度

## Completed

- T01 — 历史列表/事件/恢复 wire client 与防御性 DTO：`GET /api/v1/session-history`、`GET /api/v1/session-history/{id}/events`、`POST /api/v1/sessions`（缺省 `{}` 或仅含 `resume_session_id`）按 transport 4.2/4.5.1 消费；非法 ID/cursor/limit 在发请求前失败；稳定码不透传后端正文；共享 `transport-v1.json` 样例。2026-09-01 主 Agent 复跑 Web lint/type-check/test(42 passed)/build/`git diff --check` 通过。

## Current State

- 2026-09-01 已完成工作流初始化：`requirement.md`、`ARCHITECTURE.md`、`TASKS.md`、`AGENTS.md`、`PROMPT_TEMPLATE.md`、`PROGRESS.md`、`DECISIONS.md` 就位，`validate_workflow.py` 结构校验通过。
- 依赖前序工作流均已交付：Web 前端 `../web-frontend/`（T01–T10）与后端/适配层历史/恢复能力 `../backend-history-discover/`（T01–T06）。
- T01 已验收。可观察行为：
  - `listSessionHistory({limit?, cursor?})` 请求 `GET /api/v1/session-history`；`limit` 1..100（缺省不发送，由服务端默认 50）、`cursor` 作为不透明 ASCII（≤256）经 `encodeURIComponent` 原样回送，非法值在发请求前分别以 `invalid_history_limit` / `invalid_history_cursor` 失败。
  - `readSessionHistoryEvents(sessionId, {since?, limit?})` 请求 `GET /api/v1/session-history/{session_id}/events`；`session_id` 经 `session_...` 安全 token 校验后再 `encodeURIComponent`；`since` 0..MAX_SAFE_INTEGER、`limit` 1..200（默认不发送）；非法 ID/since/limit 在发请求前失败。
  - `createSession(resumeSessionId?)` 缺省（或第一参数为 `AbortSignal`）发送 `{}`；带值时 body 恰好只含 `resume_session_id`。既有 `useAgentSession` 的 `createSession(signal)` 调用点保持正确。POST 不自动重放。
  - 稳定码 `invalid_history_id`/`invalid_history_cursor`/`invalid_history_limit`/`history_not_found`/`history_unavailable`/`invalid_resume`/`session_exists` 使用规范表英文短句，不透传后端 message；未知码回退安全默认。
  - DTO parser 对未知/缺失/截断/坏 diagnostics/超界/非法类型安全降级；`sessions` 截到 100；空事件页强制 `has_more=false` 且 `next_cursor=null`；`transport_session_id` 与 canonical `session_id` 使用 branded 类型分离。
  - 共享样例写入 `web/src/domain/fixtures/transport-v1.json`；Python `tests/transports/test_http_transport.py` 与 `tests/integration/test_http_history.py` 增加对该 JSON 关键字段的最小结构断言。未改 HTTP 行为或其它 Python 产品代码。未实现 composable、侧边栏、App 布局或视觉。

## Known Issues

- 「切换 session」在 wire 上是「先 DELETE 当前、再 POST resume」的串行操作（单活跃 session 规则）；先终结后 resume 失败会导致当前 session 已终结且无活跃 session，须 fail-closed 提示新建，不自动重建（见 `ARCHITECTURE.md` §3.3、`DECISIONS.md`）。
- resume + live SSE 不 replay 历史事件；目标 session 历史消息须由有限历史读取端点补齐后再接续 SSE，属于新增的 hydration 流程，需覆盖幂等/跳号/多页/截断证据。
- 活跃 turn/等待授权时切换是否需要显式确认，目前为可逆规划假设（默认终结当前 + 一次确认）；若用户另有要求需在实现前更新需求、架构与决策。
- 视觉「参考 Codex 设计」为方向性描述，具体信息层级与折叠交互在 T06 落地并需人工视觉证据；南大金色仍为产品可访问 token，非校方官方 HEX。
- 真实模型网关、真实浏览器与公网部署不在本轮；相关证据只能标注为离线/脚本化，不得冒充真实验证。
- 浏览器 `since` 只能精确表示 `Number.MAX_SAFE_INTEGER`（2^53-1）以内的整数，是 wire `0..2^63-1` 的 JS 安全子集；超出安全整数的值在发请求前按 `invalid_history_cursor` 拒绝。

## Next Recommended Task

- T02 — 交付 resume 旅程与历史事件 hydration。依赖 T01；按 `ARCHITECTURE.md` §3.2 封装终结→RESET→resume 创建→历史 hydration→接续 SSE，排除侧边栏与 `useSessionHistory`。
