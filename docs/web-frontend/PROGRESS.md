# CodingAgentNeo Web 前端进度

## Completed

- T01 — 已交付共享 `AgentBackendService` 与显式 `InProcessAdapter`：`backend.py` 收敛为命令/Port/异常，worker、事件缓冲、授权通道和具体执行职责位于 `backend_service.py`；组装层提供 `build_agent_backend`/`build_in_process_adapter`，CLI 默认经 In-process binding 接入；旧 `LocalAgentBackend`/`build_local_backend` 保留为无分叉兼容 facade。主 Agent 验收证据为定向测试 44 passed、全量测试 268 passed，Ruff lint/format、build、CLI help、workflow validator、静态依赖扫描与 diff check 均通过；在 T01 完成时 HTTP/SSE、Vue 和 T02 尚未实现。
- T02 — 已实现前端无关的 `/api/v1` HTTP/SSE ASGI adapter、wire DTO/command decoder、单活跃 session registry、SSE `since`/`Last-Event-ID`/keepalive、Host/Origin 回环校验、错误/关闭映射和独立 `coding-agent-neo-http` composition root。HTTP 包只面对 `AgentBackend` Port 与注入 factory；真实共享 `build_agent_backend` 注入测试证明路径不经过 In-process Adapter。session-owned event pump 从 cursor 0 缓存 canonical 历史，各 SSE subscriber 可独立取消与按 cursor 补回，不累积连接线程。主 Agent 验收证据为定向 HTTP/shared conformance 14 passed、全量 pytest 280 passed，Ruff lint/format、Python build、HTTP CLI help、workflow validator、依赖/secret/包内容扫描与 diff check 均通过；仅有第三方 TestClient 弃用警告，fake 与 scripted service 证据不代表真实模型或公网部署。
- T03 — 已建立独立 `web/` Vue 3 + Vite + TypeScript 单页工程、npm lockfile、Vitest/Vue Test Utils、ESLint、类型检查和生产构建命令，并提供明确说明“尚未连接 Agent”的占位页。Web package 只依赖 Vue 与前端构建/测试工具，不依赖 Python、Agent 内部包、Router、Pinia 或 UI 组件框架；根 `.gitignore` 排除 `node_modules`、`web/dist` 与 coverage。Node v24.3.0/npm 11.4.2 下实测 `npm --prefix web ci`、lint、type-check、test（1 passed）、build、`git diff --check` 均通过；Vite 开发服务启动并以 curl 验证页面入口后由 Ctrl+C 停止。未执行 Agent HTTP/SSE、真实模型或真实浏览器交互，均留待后续任务。
- T04 — 已交付浏览器侧 Agent HTTP/SSE wire client、v1 命令/响应 DTO 与稳定错误归一化、SSE `since`/`Last-Event-ID` 游标消费（POST 不自动重放）、防御性 EventEnvelope parser、纯 reducer、跳号诊断/重复幂等/未知与截断降级、session composable 和仅保存 transport ID/cursor 的 localStorage 核。命令 gate 明确区分 connecting、turn running、WAITING_FOR_APPROVAL、COMPLETED_TURN、终止态与关闭/未知命令结果；授权只接受事件 envelope 的原始非空关联 ID，事件内容仅作为不可信文本投影。Interrupt/CloseSession 的可选 reason、无 reason 及非法字段均由 client contract tests 覆盖；浏览器契约测试与 `tests/transports/test_http_transport.py` 读取同一 `web/src/domain/fixtures/transport-v1.json` wire 样例。2026-09-01 实测 Web 定向 test 18 passed、全量 test 19 passed、lint、type-check、build，以及 `.venv/bin/python -m pytest tests/transports/test_http_transport.py` 11 passed（Starlette 第三方弃用警告）；未执行真实模型、公网部署或真实浏览器交互。

## Current State

- 2026-08-31 已完成 Bootstrap 及两轮 Change control：`docs/agent-backend-interface.md` 定义 Port 与共享 Backend Service 语义；`docs/agent-transport-interface.md` 是并列 In-process/HTTP binding、HTTP wire/event schema 与共享 adapter 规则的唯一前端接入权威。前端实现 adapter 接入时需要且只需要参考该文档。
- 既有 Python Agent 后端与 CLI 基线保持不变；HTTP/SSE Agent binding 已可独立运行，`web/` 目前可独立启动未连接占位页。
- 当前增量已完成 T01/T02/T03/T04；T04 仅交付网络接入和 session 状态核，尚未交付完整 timeline、approval dialog、最终视觉、刷新重连旅程或静态组合入口。

## Known Issues

- FastAPI + SSE、Vue TypeScript/npm、仅回环部署和金色 Web token 仍是可逆技术选择；用户已明确确认 adapter 所有权和显式 In-process 拆分方向。
- 南京大学官方规范明确的是标准紫 CMYK 值；架构中的紫色 HEX 是屏幕近似换算，金色 HEX 是产品视觉 token，不声称为校方发布的官方数字色值。
- `backend.py` 的旧实现名称仅通过懒兼容别名保留；共享实现和执行职责已在 `backend_service.py`，CLI 使用 `transports/in_process.py` 的薄 binding。
- HTTP binding、T03 Web 工程与 T04 浏览器 wire/session 核已交付；完整 timeline、Vite 开发代理、刷新重连和静态资源组合仍属于后续任务。
- 测试使用 fake backend 与 scripted model；live conformance 仅验证本地 uvicorn/HTTP wiring，未执行真实模型网关、真实浏览器或公网/局域网部署验证。
- 首版规划不支持服务器重启后的 Web session resume；只支持对仍存活本地进程的刷新重连。
- T03 的额外 `npm audit --omit=dev` 在当前华为云 npm 镜像上因 audit POST 端点返回 405，未形成依赖审计通过证据；任务要求的 `npm ci`、lint、type-check、test 和 build 均已通过。
- T04 的 SSE client 暴露显式 start/stop 与 `needsResubscribe` 诊断，但不在本卡自动执行刷新重连退避；该旅程留待 T07。测试使用同一 wire fixture、fake fetch 和 T02 HTTP 契约，未声称真实模型或真实浏览器证据。

## Next Recommended Task

- T05 — 交付任务、assistant 回复与事件时间线闭环。
