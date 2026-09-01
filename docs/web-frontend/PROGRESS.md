# CodingAgentNeo Web 前端进度

## Completed

- T01 — 已交付共享 `AgentBackendService` 与显式 `InProcessAdapter`：`backend.py` 收敛为命令/Port/异常，worker、事件缓冲、授权通道和具体执行职责位于 `backend_service.py`；组装层提供 `build_agent_backend`/`build_in_process_adapter`，CLI 默认经 In-process binding 接入；旧 `LocalAgentBackend`/`build_local_backend` 保留为无分叉兼容 facade。主 Agent 验收证据为定向测试 44 passed、全量测试 268 passed，Ruff lint/format、build、CLI help、workflow validator、静态依赖扫描与 diff check 均通过；在 T01 完成时 HTTP/SSE、Vue 和 T02 尚未实现。
- T02 — 已实现前端无关的 `/api/v1` HTTP/SSE ASGI adapter、wire DTO/command decoder、单活跃 session registry、SSE `since`/`Last-Event-ID`/keepalive、Host/Origin 回环校验、错误/关闭映射和独立 `coding-agent-neo-http` composition root。HTTP 包只面对 `AgentBackend` Port 与注入 factory；真实共享 `build_agent_backend` 注入测试证明路径不经过 In-process Adapter。session-owned event pump 从 cursor 0 缓存 canonical 历史，各 SSE subscriber 可独立取消与按 cursor 补回，不累积连接线程。主 Agent 验收证据为定向 HTTP/shared conformance 14 passed、全量 pytest 280 passed，Ruff lint/format、Python build、HTTP CLI help、workflow validator、依赖/secret/包内容扫描与 diff check 均通过；仅有第三方 TestClient 弃用警告，fake 与 scripted service 证据不代表真实模型或公网部署。
- T03 — 已建立独立 `web/` Vue 3 + Vite + TypeScript 单页工程、npm lockfile、Vitest/Vue Test Utils、ESLint、类型检查和生产构建命令，并提供明确说明“尚未连接 Agent”的占位页。Web package 只依赖 Vue 与前端构建/测试工具，不依赖 Python、Agent 内部包、Router、Pinia 或 UI 组件框架；根 `.gitignore` 排除 `node_modules`、`web/dist` 与 coverage。Node v24.3.0/npm 11.4.2 下实测 `npm --prefix web ci`、lint、type-check、test（1 passed）、build、`git diff --check` 均通过；Vite 开发服务启动并以 curl 验证页面入口后由 Ctrl+C 停止。未执行 Agent HTTP/SSE、真实模型或真实浏览器交互，均留待后续任务。
- T04 — 已交付浏览器侧 Agent HTTP/SSE wire client、v1 命令/响应 DTO 与稳定错误归一化、SSE `since`/`Last-Event-ID` 游标消费（POST 不自动重放）、防御性 EventEnvelope parser、纯 reducer、跳号诊断/重复幂等/未知与截断降级、session composable 和仅保存 transport ID/cursor 的 localStorage 核。命令 gate 明确区分 connecting、turn running、WAITING_FOR_APPROVAL、COMPLETED_TURN、终止态与关闭/未知命令结果；授权只接受事件 envelope 的原始非空关联 ID，事件内容仅作为不可信文本投影。Interrupt/CloseSession 的可选 reason、无 reason 及非法字段均由 client contract tests 覆盖；浏览器契约测试与 `tests/transports/test_http_transport.py` 读取同一 `web/src/domain/fixtures/transport-v1.json` wire 样例。2026-09-01 实测 Web 定向 test 18 passed、全量 test 19 passed、lint、type-check、build，以及 `.venv/bin/python -m pytest tests/transports/test_http_transport.py` 11 passed（Starlette 第三方弃用警告）；未执行真实模型、公网部署或真实浏览器交互。
- T05 — 已将 session composable 接入 Vue 页面：自动创建 transport session、非空任务 composer、一次性提交锁、用户/assistant/运行/错误/结束事件的 sequence 时间线、`turn_end.assistant_text` 优先且回退最近 assistant 文本、COMPLETED_TURN follow-up 解锁与终止态锁定。长文本按安全纯文本有界展示并可展开；未知、缺失或截断 payload 降级为事实提示；tool failure 仅作运行事件展示，不改写为 session FAILED。HTTP 400/409/410 映射为安全可恢复提示，POST 不自动重放；新增 App/timeline 测试。2026-09-01 实测 Web lint、type-check、全量 test 24 passed、build；`.venv/bin/python -m pytest tests/transports tests/integration/test_frontend_contract.py` 在显式 `NO_PROXY/no_proxy=127.0.0.1,localhost` 下 19 passed（含真实 `AgentBackendService` + HTTP adapter 的 scripted fake 无授权 turn 到 `turn_end`），仅有 Starlette 第三方弃用警告。未执行真实模型网关、真实浏览器或公网部署；approval 操作、精细工具卡、刷新重连/退避和最终视觉仍不在本卡。
- T06 — 已交付按 envelope `correlation_id` 聚合的工具生命周期卡片（工具/授权/策略/结果、脱敏摘要、状态、耗时、退出码、超时与截断）；唯一 pending approval dialog 只回送事件原始非空 request ID，批准/拒绝单击锁定并等待匹配 `policy_decision`，Escape、断线、关闭与无效 ID 均 fail-closed；RUNNING/WAITING 状态提供一次性 Stop，`Interrupt` 202 后锁到 `INTERRUPTED` turn 边界。新增 reducer/composable/component/conformance 覆盖批准、拒绝、超时、ID 不匹配、断开与普通 tool failure。2026-09-01 实测 Web test 39 passed、lint、type-check、build；任务指定 Python 命令首次受本机代理环境影响返回 HTTP 502，显式 `NO_PROXY/no_proxy=127.0.0.1,localhost` 后 31 passed，仅有 Starlette 第三方弃用警告。未执行真实模型网关、真实浏览器或公网部署；刷新重连/退避、最终视觉和组合入口仍不在本卡。

- T07 — 已交付成功 `COMPLETED_TURN` 后在同一 transport/backend session 的线性 follow-up、刷新时先 GET 查询持久化 transport ID 再以浏览器最后成功 cursor 重订阅，以及仅 GET/SSE 的有限指数退避。事件流断开不改变 Agent、不重放 POST；重复 sequence 幂等忽略，跳号保留 `sequence_gap` 诊断并从旧 cursor 重新订阅；重新 attach 得到 RUNNING 快照时保持 fail-closed，直到消费 canonical turn_end 或 session_end，并提示可以 Stop 或结束 session；404/410 或 `closed` 清除 stale ID、进入失联提示新建，不声称服务器重启后的历史恢复。新增显式 DELETE 结束入口，CloseSession/DELETE 后清理状态并禁止命令；组件卸载只停止 SSE，不发送关闭。2026-09-01 实测 Web test 45 passed、lint、type-check、build；任务指定 Python 命令在默认本机代理环境下 1 个 live HTTP fixture 返回 502，显式 `NO_PROXY/no_proxy=127.0.0.1,localhost` 后 27 passed，仅有 Starlette 第三方弃用警告。未执行真实模型网关、真实浏览器或公网部署；T08 视觉/可访问性与 T09 组合入口仍待后续任务。
- T08 — 已完成既有 Web 旅程的极简浅色紫金视觉、CSS token、响应式断点、空/加载/错误态、状态图标与 `aria-live` 语义；桌面与 360px 窄屏保证无横向溢出，composer、timeline、工具卡、approval dialog 与 Stop 保持可用。授权 dialog 新增初始焦点、Tab/focusout 约束、Escape fail-closed 和 opener 焦点恢复；全局 focus-visible、`aria-busy` 与 `prefers-reduced-motion` 路径已加入。2026-09-01 实测 `npm --prefix web run lint`、`type-check`、`test`（46 passed）、`build` 均通过；Codex In-app Browser 人工检查 1280×720 和 360×800、完整 scripted 旅程、键盘路径、CSSOM reduced-motion 规则、对比度与控制台无误。实际矩阵与限制见 [T08_VISUAL_CHECK.md](T08_VISUAL_CHECK.md)；未执行真实模型、公网部署或 T09 组合入口。

## Current State

- 2026-08-31 已完成 Bootstrap 及两轮 Change control：`docs/agent-backend-interface.md` 定义 Port 与共享 Backend Service 语义；`docs/agent-transport-interface.md` 是并列 In-process/HTTP binding、HTTP wire/event schema 与共享 adapter 规则的唯一前端接入权威。前端实现 adapter 接入时需要且只需要参考该文档。
- 既有 Python Agent 后端与 CLI 基线保持不变；HTTP/SSE Agent binding 已可独立运行，`web/` 目前可独立启动未连接占位页。
- 当前增量已完成 T01/T02/T03/T04/T05/T06/T07/T08；T08 已交付最终视觉与可访问性打磨，服务器重启后的历史恢复和静态组合入口仍属于后续任务。

## Known Issues

- FastAPI + SSE、Vue TypeScript/npm、仅回环部署和金色 Web token 仍是可逆技术选择；用户已明确确认 adapter 所有权和显式 In-process 拆分方向。
- 南京大学官方规范明确的是标准紫 CMYK 值；架构中的紫色 HEX 是屏幕近似换算，金色 HEX 是产品视觉 token，不声称为校方发布的官方数字色值。
- `backend.py` 的旧实现名称仅通过懒兼容别名保留；共享实现和执行职责已在 `backend_service.py`，CLI 使用 `transports/in_process.py` 的薄 binding。
- HTTP binding、T03 Web 工程与 T04 浏览器 wire/session 核已交付；完整 timeline、刷新重连与 T07 生命周期收口已交付，Vite 开发代理和静态资源组合仍属于后续任务。
- 测试使用 fake backend 与 scripted model；live conformance 仅验证本地 uvicorn/HTTP wiring，未执行真实模型网关、真实浏览器或公网/局域网部署验证。
- 首版规划不支持服务器重启后的 Web session resume；只支持对仍存活本地进程的刷新重连。
- T03 的额外 `npm audit --omit=dev` 在当前华为云 npm 镜像上因 audit POST 端点返回 405，未形成依赖审计通过证据；任务要求的 `npm ci`、lint、type-check、test 和 build 均已通过。
- T07 的刷新重连仅承诺仍存活 Agent HTTP 进程中的 transport session；服务器重启或未知/已关闭 session 会清除标识并要求新建，不能恢复历史 timeline。测试使用 fake fetch、scripted service 和本地 HTTP wiring，未声称真实模型、真实浏览器或公网部署证据。
- T08 的 reduced-motion 规则已在真实 IAB CSSOM 中确认，但该浏览器运行时未提供直接切换系统 reduced-motion 偏好的能力；因此未伪造强制偏好截图。无装饰动画的正常路径、静态规则与自动化质量门均已检查。

## Next Recommended Task

- T09 — 交付与通用 Agent HTTP 分离的 Web 组合入口。
