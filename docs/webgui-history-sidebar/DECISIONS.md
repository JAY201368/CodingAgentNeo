# CodingAgentNeo Web GUI 历史侧边栏决策日志

本文件按时间追加持久、非显然且影响下游的选择。Bootstrap 条目保留当时规划语境；当前实施与验收完成态以 `TASKS.md` 和 `PROGRESS.md` 为准。

## 2026-09-01 — Bootstrap 定位：落地被延期的 Web UI，纯 Web 增量

- 选择：新建 `docs/webgui-history-sidebar/` 工作流，只在 `web/` 内消费既有历史/恢复 wire 契约，不改 Python、`transports/`、`assembly.py` 或任何 `docs/agent-*.md`/`docs/backend-history-discover/` 权威规范。
- 理由与替代：后端与适配层的历史发现/恢复能力已由 `../backend-history-discover/` 交付，且 `../agent-transport-interface.md` 明确「Web UI 消费延期」。把该消费实现放到独立 Web 工作流，可保持后端契约冻结、边界清晰；若混入后端工作流会模糊「后端能力 vs 前端消费」的所有权。
- 后果：本工作流 supersede `../web-frontend/` 中「首版不提供历史 resume UI / 不实现历史 session 浏览器」的排除项（历史条目保留为当时事实）；任何需要后端新增能力的诉求都必须回到跨工作流变更控制，而不是在本工作流内改 Python。

## 2026-09-01 — 切换 session 为「先终结当前、再创建恢复」串行操作

- 选择：点击侧边栏 session 时，先 `DELETE /api/v1/sessions/{transport_id}` 终结当前 transport session，再 `POST /api/v1/sessions {"resume_session_id"}` 创建恢复 session；两步串行，不并发。
- 理由与替代：wire 规范的单活跃 session 规则要求存在活跃 session 时创建返回 `409 session_exists`，因此无法「先创建后切换」。这与需求「默认终结当前 session 并 resume 到目标 session」一致。
- 后果：存在「已终结当前但 resume 创建失败」的窗口。此时没有活跃 session，UI 必须 fail-closed 显示安全错误并提供新建入口，不自动重建、不自动重放。`resumable`/cursor 不是授权，恢复以后端重验证为准。

## 2026-09-01 — 历史消息由有限历史读取 hydration，再接续 live SSE

- 选择：resume 成功后，从 `since=0` 分页调用 `GET /api/v1/session-history/{session_id}/events`，把 canonical envelope 按 sequence 升序逐条经既有 reducer 的 `EVENT` action 注入以重现历史消息；hydration 到 `has_more=false` 后，从 reducer 当前 cursor 起 `startEvents()` 接续 live SSE。
- 理由与替代：resume + live SSE 不 replay 历史事件（wire 规范 4.5.1），只订阅 SSE 无法显示历史消息。历史读取是有限 JSON 快照，复用既有 reducer/`projectTimeline` 即可无第二套投影逻辑地重现消息，并天然获得重复幂等、跳号降级、未知/截断安全降级。
- 后果：切换前必须 `RESET` 清空旧投影；hydration 使用目标 canonical `session_id`（与 `transport_session_id` 分离）。hydration 与后续 SSE 的 cursor 必须衔接，避免重复或跳过；历史 read 的并发追加只影响 `has_more`/摘要，不破坏 cursor 语义。

## 2026-09-01 — 布局：侧边栏 + 右侧居中主区，标题移入侧边栏（参考 Codex）

- 选择：`App.vue` 改为「左侧固定侧边栏 + 右侧居中主区」；页面标题移到侧边栏顶部，session 列表在其下；原有整页对话流、固定底部 composer、授权与消息尾部动态入口迁入右侧主区并水平居中。窄屏侧边栏折叠/抽屉化。
- 理由与替代：需求明确要求侧边栏、右侧居中与标题移入侧边栏，并「总体参考 Codex 设计」。保留既有对话流与 composer 语义可最小化对已验收 Web 旅程的破坏；不引入 Router/Pinia/UI 框架以守住轻量前端边界。
- 后果：既有整页滚动、固定 composer、按 turn 折叠等展示契约在主区内保持；侧边栏是新增视图层，不改变命令/事件/授权/安全契约。Codex 的具体视觉细节为方向参考，T06 落地并留人工证据，不复制其品牌资产。

## 2026-09-01 —（可逆规划假设）活跃 turn 时切换给出一次显式确认

- 选择：默认行为是点击即终结当前并 resume；但当前 session 存在活跃 turn 或等待授权时，先弹出一次显式确认再终结，确认取消则不改变当前 session。
- 理由与替代：需求说「默认终结当前 session」，未明确活跃 turn 中途切换的期望。直接无提示终结可能让用户误触丢失正在运行的工作；一次确认在不违背「默认终结」的前提下提供 fail-safe。替代方案是完全无确认（更贴近字面「默认」）或运行中禁止切换（过度限制）。
- 后果：这是可逆规划假设，标注在此并在 `PROGRESS.md`/`TASKS.md` T05 体现。若用户希望「运行中也直接无提示切换」或「运行中禁止切换」，须在实现 T05 前更新 `requirement.md`、`ARCHITECTURE.md` 与本条决策；在确认前 T05 仍可实现，但确认交互须易于调整。
