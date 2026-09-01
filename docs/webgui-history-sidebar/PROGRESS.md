# CodingAgentNeo Web GUI 历史侧边栏进度

## Completed

- 无。本工作流处于 Bootstrap 完成、实现未开始状态；尚无任务被验收。

## Current State

- 2026-09-01 已完成工作流初始化：`requirement.md`、`ARCHITECTURE.md`、`TASKS.md`、`AGENTS.md`、`PROMPT_TEMPLATE.md`、`PROGRESS.md`、`DECISIONS.md` 就位，`validate_workflow.py` 结构校验通过。
- 依赖前序工作流均已交付：Web 前端 `../web-frontend/`（T01–T10）与后端/适配层历史/恢复能力 `../backend-history-discover/`（T01–T06）。后端已提供 `GET /api/v1/session-history`、`GET /api/v1/session-history/{id}/events`（有限 JSON）与 `POST /api/v1/sessions {resume_session_id}`；Web UI 消费为当时明确延期项，本工作流负责落地。
- 尚未修改任何 `web/` 产品代码；任务 DAG 为 T01 → (T02, T03) → T04 → T05 → T06 → T07，一次只派发一个。

## Known Issues

- 「切换 session」在 wire 上是「先 DELETE 当前、再 POST resume」的串行操作（单活跃 session 规则）；先终结后 resume 失败会导致当前 session 已终结且无活跃 session，须 fail-closed 提示新建，不自动重建（见 `ARCHITECTURE.md` §3.3、`DECISIONS.md`）。
- resume + live SSE 不 replay 历史事件；目标 session 历史消息须由有限历史读取端点补齐后再接续 SSE，属于新增的 hydration 流程，需覆盖幂等/跳号/多页/截断证据。
- 活跃 turn/等待授权时切换是否需要显式确认，目前为可逆规划假设（默认终结当前 + 一次确认）；若用户另有要求需在实现前更新需求、架构与决策。
- 视觉「参考 Codex 设计」为方向性描述，具体信息层级与折叠交互在 T06 落地并需人工视觉证据；南大金色仍为产品可访问 token，非校方官方 HEX。
- 真实模型网关、真实浏览器与公网部署不在本轮；相关证据只能标注为离线/脚本化，不得冒充真实验证。

## Next Recommended Task

- T01 — 交付历史列表/事件/恢复的 wire client 与防御性 DTO。无依赖，是后续列表 composable（T03）与恢复旅程（T02）的共同前置，先稳定 wire 消费与错误/DTO 边界。
