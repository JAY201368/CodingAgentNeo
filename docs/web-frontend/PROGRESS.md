# CodingAgentNeo Web 前端进度

## Completed

- T01 — 已交付共享 `AgentBackendService` 与显式 `InProcessAdapter`：`backend.py` 收敛为命令/Port/异常，worker、事件缓冲、授权通道和具体执行职责位于 `backend_service.py`；组装层提供 `build_agent_backend`/`build_in_process_adapter`，CLI 默认经 In-process binding 接入；旧 `LocalAgentBackend`/`build_local_backend` 保留为无分叉兼容 facade。主 Agent 验收证据为定向测试 44 passed、全量测试 268 passed，Ruff lint/format、build、CLI help、workflow validator、静态依赖扫描与 diff check 均通过；HTTP/SSE、Vue 和 T02 仍未实现。

## Current State

- 2026-08-31 已完成 Bootstrap 及两轮 Change control：`docs/agent-backend-interface.md` 定义 Port 与共享 Backend Service 语义；`docs/agent-transport-interface.md` 独立定义并列 In-process/HTTP binding。前端只参考对应 binding 与共享 adapter 规则。
- 既有 Python Agent 后端与 CLI 基线保持不变；仓库尚无 `web/`、HTTP/SSE 传输适配层或 Web 产品代码，不能声称 Web 前端可运行。
- 当前增量已完成 T01；后续 T02 HTTP/SSE、T03 Web 工程及更后续任务未开始，不得提前声称可用。

## Known Issues

- FastAPI + SSE、Vue TypeScript/npm、仅回环部署和金色 Web token 仍是可逆技术选择；用户已明确确认 adapter 所有权和显式 In-process 拆分方向。
- 南京大学官方规范明确的是标准紫 CMYK 值；架构中的紫色 HEX 是屏幕近似换算，金色 HEX 是产品视觉 token，不声称为校方发布的官方数字色值。
- `backend.py` 的旧实现名称仅通过懒兼容别名保留；共享实现和执行职责已在 `backend_service.py`，CLI 使用 `transports/in_process.py` 的薄 binding。
- 浏览器到 Agent 之间仍无网络适配；新增 wire 规范是规划契约，不是 HTTP 已实现证据。
- 首版规划不支持服务器重启后的 Web session resume；只支持对仍存活本地进程的刷新重连。

## Next Recommended Task

- T02 — 交付前端无关的 Agent HTTP/SSE Adapter；本轮未实现 HTTP、Vue 或 Web composition launcher。
