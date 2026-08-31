# CodingAgentNeo Web 前端决策日志

本文件按时间追加持久、非显然且会影响下游的选择。Bootstrap 阶段条目均为待用户审阅的可逆规划决定，不代表实现已经完成。

## 2026-08-31 — ARCH 以 JSON command + SSE 桥接既有 Backend

- 选择：规划一个与 Agent 后端同进程的 FastAPI/uvicorn 适配层；浏览器用 POST 发送四种既有命令，用 SSE 按 sequence 消费 canonical `EventEnvelope`。
- 理由与替代：SSE 符合单向、有序、长连接事件流，命令仍保持显式请求；比 WebSocket 双向协议更小且不暗示支持 steering。直接从浏览器调用 Python 对象不可行，轮询会弱化长事件流语义。
- 后果：必须新增 `/api/v1` 适配层契约和契约测试；Agent 后端语义现由重命名后的 `docs/agent-backend-interface.md` 控制。用户确认前不得实施。

## 2026-08-31 — ARCH 首版是仅回环、单活跃 session 的本地 UI

- 选择：生产演示由 Python 服务同源托管 Vite 构建物，只监听 `127.0.0.1`，registry 同时最多一个 backend；不提供公网 host、宽泛 CORS或远程认证。
- 理由与替代：现有后端和 bash 均是单用户本地信任模型。开放局域网/公网会立即引入认证、TLS、CSRF、会话所有权和远程命令执行风险，超出“轻量前端”范围。
- 后果：所有端点必须验证 Host/Origin，浏览器不接触 API Key；未来远程访问属于安全与部署契约变更。

## 2026-08-31 — ARCH Vue 核心保持小型、无重型状态和组件框架

- 选择：使用 Vue 3 + Vite + TypeScript、Composition API、一个 session composable、纯事件 reducer 和原生 CSS；不引入 Router、Pinia 或 UI 组件框架。
- 理由与替代：首版只有单页、单 session 与少量状态，用额外框架会扩大依赖和解释成本；TypeScript 有助于守住事件/命令边界。
- 后果：T01 固化具体支持版本和 lockfile；若状态规模在实现中真实超出这一边界，先更新架构和任务，而不是在 Worker 内临时加框架。

## 2026-08-31 — ARCH 紫金视觉使用官方紫来源与可访问 Web token

- 选择：南京大学标准紫依据校方视觉形象规范的 CMYK `50/100/0/40`，在 Web 中近似为 `#4D0099`；产品金色暂用 `#C7A34B` 作装饰、`#6B4F00` 作浅底文字，背景以暖白和白色为主。
- 理由与替代：[南京大学党委宣传部的视觉形象规范](https://xcb.nju.edu.cn/info/1551/2481.htm)及其正式通知明确标准紫 CMYK，但未在可核对正文中发布一个“官方金色 HEX”。因此不把金色 token冒充校方精确标准，并通过深金文本保证对比度。
- 后果：T07 必须验证 WCAG 2.2 AA、键盘和窄屏；若用户提供官方 RGB/HEX 或品牌资产，先更新架构 token 和视觉测试证据。

## 2026-08-31 — ARCH 刷新重连不等于跨进程 resume

- 选择：浏览器只保存非敏感 transport session ID 与最后成功处理的 sequence，可重连仍存活的服务器进程；首版不提供服务器重启后的历史 `--resume` UI。
- 理由与替代：现有规范明确 `resume_last_sequence`/`resume_diagnostics` 不是 `AgentBackend` Protocol，直接把它们暴露给 Web 会依赖具体实现并扩大正式契约。
- 后果：刷新补回和 POST 不重放由 T03/T06 验证；跨进程 Web resume 以后必须先把恢复元数据提升为正式后端/传输契约。

## 2026-08-31 — ARCH Agent 传输所有权与显式 In-process Adapter

- 选择：HTTP/SSE Adapter 属于 Agent 侧通用 `transports/`，与 CLI 使用的显式 In-process Adapter 并列；Vue Web App 只实现 wire client。先把 `backend.py` 中的 worker、EventStreamBuffer、ApprovalChannel/Port 和 `LocalAgentBackend` 移到 `transports/in_process.py`，再实现 HTTP。
- 理由与替代：baseline 只完成 Python 同进程的逻辑解耦，未形成语言/进程无关接入。若把 HTTP 放在 Web 侧，会让 Agent 接入能力被首个 Vue 客户端反向拥有；若不拆 In-process，实现和 port 继续同模块，也无法形成清晰并列关系。
- 后果：新增 `docs/agent-transport-interface.md`；T01 专门做无行为变化的 In-process 拆分，T02 再做通用 HTTP。旧 `LocalAgentBackend`/`build_local_backend` 只可作为兼容别名。本条 supersede 本日志首条中“HTTP 适配层作为 Web 增量所有物”的含义，但保留 JSON + SSE 技术选择。
- 任务映射：旧规划中由 T01/T02/T03 表示的 Web 基础、HTTP 和客户端职责已被新 DAG 取代；当前权威映射为 T01 In-process、T02 HTTP、T03 Web 基础、T04 Web client，刷新重连为 T07。

## 2026-08-31 — ARCH 通用 HTTP Adapter 不托管 Web 静态资源

- 选择：`transports/http/`、`coding-agent-neo-http` 与 Vue/Vite/`web/dist` 完全分离；一键 Web 演示由后续独立 `web_launcher.py` composition root 组合通用 ASGI app 和静态资源。
- 理由与替代：让通用 adapter 直接定位 `web/dist` 会产生真实 Web 产品耦合，使未来其他前端无法把它视为纯 Agent transport。完全取消一键启动又降低本地演示易用性，因此使用单独组合入口。
- 后果：T02 不能包含静态路由，T09 才能接触 HTTP app 与 `web/dist`；独立 Agent HTTP 必须在没有 Node/Web 构建物时仍可运行。本条 supersede “生产由 HTTP adapter 同源托管 `web/dist`”的旧规划。

## 2026-08-31 — ARCH 后端规范与 Adapter 接入规范分离

- 选择：将 `docs/frontend-backend-interface.md` 连同标题重命名为 `docs/agent-backend-interface.md` / “Agent 后端接口规范”，只定义 adapter 面向的 `AgentBackend` 端口及命令、事件、状态、授权和生命周期语义；`docs/agent-transport-interface.md` 独立定义 In-process 与 HTTP/SSE binding。
- 理由与替代：增加并列 adapter 后，“前后端接口”会让前端误以为应直接依赖 Python Port。实际依赖方向应是 adapter 实现读取后端规范，CLI/Web 只读取自己 adapter 的公开 binding。
- 后果：仓库内权威链接全部切换到新文件名；Python 创建入口、Iterator、异常、timeout、resume capability 移入 In-process binding，HTTP 路径/SSE 保持在 HTTP binding。文件名不保留旧入口，避免两个可能漂移的权威规范。

## 2026-08-31 — ARCH 共享 Backend Service 与两种 Adapter 真正并列

- 选择：把 worker、`EventStreamBuffer`、`ApprovalChannel/Port` 和具体 `AgentBackend` 实现归入前端无关的 `backend_service.py`；`transports/in_process.py` 只作为薄 Python binding，`transports/http/` 只作为 wire binding。两者分别依赖同一 Port 和共享 backend factory，HTTP 不经过 In-process Adapter。
- 理由与替代：旧 0.2 模型同时把两种 adapter 称为并列，又让 HTTP factory 创建 `InProcessAgentBackend`，并把两种访问方式都需要的执行运行时归入 In-process，导致概念和依赖方向冲突。仅把具体 service 改名为 In-process Adapter 不能解决该问题。
- 后果：T01 现同时交付纯 Port、共享 Backend Service 和薄 In-process Adapter；T02 只注入共享 backend factory。兼容 `LocalAgentBackend`/`build_local_backend` 只是 facade，不得成为 HTTP 依赖。本条 supersede 先前“worker/事件缓冲/授权通道归 In-process Adapter”和“HTTP 创建 In-process backend”的描述。
- 任务映射更正：Vue 工程基础为 T03，浏览器 HTTP client 为 T04，刷新重连为 T07，视觉与可访问性为 T08。本映射 supersede 日志中残留的 T01、T03/T06 和 T07 旧编号。
