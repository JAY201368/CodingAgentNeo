# CodingAgentNeo Web 前端进度

## Completed

- 暂无已接受的增量实现任务；T01～T10 均未开始。

## Current State

- 2026-08-31 已完成 Bootstrap 及两轮 Change control：`docs/agent-backend-interface.md` 定义 Port 与共享 Backend Service 语义；`docs/agent-transport-interface.md` 独立定义并列 In-process/HTTP binding。前端只参考对应 binding 与共享 adapter 规则。
- 既有 Python Agent 后端与 CLI 基线保持不变；仓库尚无 `web/`、Web 传输适配层或 Web 产品代码，不能声称 Web 前端可运行。
- 当前模式为 Change control / Plan。本轮只改文档，没有移动 `backend.py` 或实现任何 adapter。

## Known Issues

- FastAPI + SSE、Vue TypeScript/npm、仅回环部署和金色 Web token 仍是可逆技术选择；用户已明确确认 adapter 所有权和显式 In-process 拆分方向。
- 南京大学官方规范明确的是标准紫 CMYK 值；架构中的紫色 HEX 是屏幕近似换算，金色 HEX 是产品视觉 token，不声称为校方发布的官方数字色值。
- 当前 `backend.py` 仍混合纯 port、EventStreamBuffer、ApprovalChannel/Port 和 `LocalAgentBackend`；规划中的共享 `backend_service.py` 和薄 `transports/in_process.py` 尚未创建。
- 浏览器到 Agent 之间仍无网络适配；新增 wire 规范是规划契约，不是 HTTP 已实现证据。
- 首版规划不支持服务器重启后的 Web session resume；只支持对仍存活本地进程的刷新重连。

## Next Recommended Task

- T01 — 拆分共享 Backend Service 与显式 In-process Adapter。该卡无依赖，先稳定 Port/Service/并列 adapter 边界；T03 也无依赖，但默认在 T01/T02 后推进 Web 轨道。
