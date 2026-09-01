# CodingAgentNeo Web GUI 历史侧边栏进度

## Completed

- T01 — 历史列表/事件/恢复 wire client 与防御性 DTO：`GET /api/v1/session-history`、`GET /api/v1/session-history/{id}/events`、`POST /api/v1/sessions`（缺省 `{}` 或仅含 `resume_session_id`）按 transport 4.2/4.5.1 消费；非法 ID/cursor/limit 在发请求前失败；稳定码不透传后端正文；共享 `transport-v1.json` 样例。2026-09-01 主 Agent 复跑 Web lint/type-check/test(42 passed)/build/`git diff --check` 通过。
- T02 — resume 旅程与历史 hydration：`resumeSession` 按终结→RESET→创建→hydration→SSE 执行；绑定新 transport 时 hydration 从 cursor 0 起步；创建失败 fail-closed；localStorage 只写新 transport ID/cursor。2026-09-01 主 Agent 复跑 lint/type-check/`test -- src/composables`（30 passed）/build 通过。

## Current State

- 2026-09-01 已完成工作流初始化：`requirement.md`、`ARCHITECTURE.md`、`TASKS.md`、`AGENTS.md`、`PROMPT_TEMPLATE.md`、`PROGRESS.md`、`DECISIONS.md` 就位，`validate_workflow.py` 结构校验通过。
- 依赖前序工作流均已交付：Web 前端 `../web-frontend/`（T01–T10）与后端/适配层历史/恢复能力 `../backend-history-discover/`（T01–T06）。
- T01 已验收（见 Completed）。
- T02 已验收。可观察行为：
  - `useAgentSession().resumeSession(historySessionId)` 按「校验 canonical ID → 拒绝 switching 重入 → `stopEvents` → `DELETE` 当前 transport（无则跳过；404/410 视为已关闭）→ `RESET` → `POST /sessions {resume_session_id}` → 绑定新 transport 且 hydration 从 cursor 0 开始 → 分页 `readSessionHistoryEvents(since=0)` 经 `EVENT` 注入 → 从 reducer 当前 cursor `startEvents()`」执行。
  - 暴露 `switching`；切换中拒绝第二次 `resumeSession`。不调用 composable `deleteSession()`（避免 `connected` 门槛与 `CLOSED`）。
  - 绑定新 transport 时 **不用** resume 响应 cursor 作为 hydration 起点；历史事件从 `since=0` 注入，由 reducer 推进 cursor；live SSE 使用 hydration 后的 reducer cursor（`sequence > cursor`）。
  - 先终结后创建失败（404/422/409 稳定码或创建阶段网络失败）进入 `SESSION_UNAVAILABLE`：无活跃 transport、不自动新建、不重放 POST；错误用 T01 客户端安全短句。DELETE 网络失败则停止、不 POST resume。
  - localStorage 只写新 transport ID/cursor，不写 historySessionId。恢复后 `finalAssistantText` / `projectTimeline` 可重现历史消息，`COMPLETED_TURN` 后可 `submitTask` follow-up。
  - 未实现 `useSessionHistory`、`HistorySidebar`、App 布局或视觉。
  - Worker 验证：`npm --prefix web run lint`、`type-check`、`test -- src/composables`（30 passed）、`build` 均通过。

## Known Issues

- 「切换 session」在 wire 上是「先 DELETE 当前、再 POST resume」的串行操作（单活跃 session 规则）；先终结后 resume 失败会导致当前 session 已终结且无活跃 session，须 fail-closed 提示新建，不自动重建（见 `ARCHITECTURE.md` §3.3、`DECISIONS.md`）。T02 已把该态交给 composable；侧边栏锁定、新建入口与活跃 turn 确认仍属 T05。
- resume + live SSE 不 replay 历史事件；目标 session 历史消息由有限历史读取 hydration 后再接续 SSE。T02 已覆盖幂等/跳号/多页/截断/未知事件；视图接线仍属 T05。
- 活跃 turn/等待授权时切换是否需要显式确认，目前为可逆规划假设（默认终结当前 + 一次确认）；若用户另有要求需在实现前更新需求、架构与决策。
- 视觉「参考 Codex 设计」为方向性描述，具体信息层级与折叠交互在 T06 落地并需人工视觉证据；南大金色仍为产品可访问 token，非校方官方 HEX。
- 真实模型网关、真实浏览器与公网部署不在本轮；相关证据只能标注为离线/脚本化，不得冒充真实验证。
- 浏览器 `since` 只能精确表示 `Number.MAX_SAFE_INTEGER`（2^53-1）以内的整数，是 wire `0..2^63-1` 的 JS 安全子集；超出安全整数的值在发请求前按 `invalid_history_cursor` 拒绝。

## Next Recommended Task

- T03 — 交付历史列表状态与分页 composable。依赖 T01；新增 `useSessionHistory`，排除历史事件读取、resume、视图与布局。
