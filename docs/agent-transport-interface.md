# CodingAgentNeo Agent 适配层接口规范

> 状态：T01 In-process Python binding 与 T02 HTTP/SSE binding 已实现
> 规范版本：0.1-draft
> 日期：2026-08-31
> 后端依据：[agent-backend-interface.md](agent-backend-interface.md)
> 实施工作流：[web-frontend/](web-frontend/)

本文定义 Agent 侧各适配层向对应前端公开的接口，使不同类型前端能够平等使用同一套后端语义。Adapter 实现者以 `agent-backend-interface.md` 为内部依据；前端不得越过 adapter 直接依赖该后端规范：CLI 参考本文第 3 节 In-process binding 与第 6 节共享规则，Web 与其他进程外前端参考第 4 节 HTTP/SSE binding 与第 6 节共享规则。

本文不改变 baseline 完成态；当前仓库已提供 T01 的 In-process Python binding 和 T02 的
HTTP/SSE 服务。Web 客户端仍只消费本文件第 4 节 binding，不直接依赖 Python 端口。

## 1. 端口与适配器

```text
CLI ── In-process Adapter ──┐
                            ├── AgentBackend Port ── Backend Service/Runtime ── Agent Core
Web/other client ── HTTP/SSE Adapter ──┘
```

- `AgentBackend Port`：adapter 面向的后端应用端口，由独立的 Agent 后端接口规范定义。
- `Backend Service/Runtime`：`AgentBackend` 的共享具体实现，拥有 worker、事件缓冲、授权通道与 Core 组装；它不是 transport adapter。
- `In-process Adapter`：对 `AgentBackend` 的薄 Python binding，负责同进程生命周期和 CLI 需要的可选 resume 元数据，不拥有 Agent 执行语义。
- `HTTP/SSE Adapter`：Agent 侧、前端无关的网络适配器，把 JSON/SSE 映射到一个由共享 backend factory 创建的 `AgentBackend`；不得依赖 In-process Adapter、Vue、Vite、静态资源或具体前端。
- Web Client：只消费本文的 wire contract，不 import Python 源码，也不拥有 Agent 决策、安全和持久化。

平等接入指语义一致，不要求所有前端使用同一种传输。CLI 可以保持同进程调用；进程外前端使用版本化 wire protocol。两种适配器必须共享 conformance suite。

## 2. 计划模块边界

| 模块 | 拥有 | 禁止拥有 |
| --- | --- | --- |
| `backend.py` | 公开 command/port/exception；前端无关语义 | worker、线程、队列、EventEmitter、SessionStore、AgentLoop、终端或 HTTP |
| `backend_service.py` | `AgentBackendService`、worker、EventStreamBuffer、ApprovalChannel/Port | CLI/HTTP I/O、Vue、transport session/route |
| `transports/in_process.py` | `InProcessAdapter`、Python binding、同进程生命周期与 resume 元数据暴露 | worker、Agent Core 决策、CLI I/O、HTTP |
| `transports/http/` | HTTP app、wire DTO、SSE、session registry、错误映射、Host/Origin 防护 | Vue、Vite、`web/dist`、Agent Core 具体对象 |
| `assembly.py` | 组装依赖并提供共享 `AgentBackendFactory`；在 composition root 组装具体 adapter | CLI/Web 展示和 HTTP 路由 |
| `http_cli.py` | Agent HTTP 服务启动与配置组合 | 静态 Web 资源和浏览器状态 |
| `web_launcher.py` | 可选地组合通用 HTTP app 与已构建静态资源 | 修改 transport/core 语义 |

计划将当前 `LocalAgentBackend` 的具体执行职责迁入 `AgentBackendService`，并新建 `InProcessAdapter` 作为薄 binding。为避免破坏 baseline 用户和测试，`LocalAgentBackend`、`build_local_backend` 可以保留为有测试的兼容 facade；兼容层不得成为 HTTP Adapter 的依赖。

## 3. In-process Adapter 接口

### 3.1 接入入口

CLI 和其他受控 Python 同进程前端通过组装层取得 adapter：

```python
from coding_agent_neo.assembly import build_in_process_adapter

backend = build_in_process_adapter(config, interactive=True, resume=None)
```

| 参数 | 类型 | 语义 |
| --- | --- | --- |
| `config` | `AppConfig` | 已解析、校验且只在 Agent 进程持有的配置 |
| `interactive` | `bool` | 是否允许通过事件请求人类授权 |
| `resume` | `str \| PathLike \| None` | 恢复线性 session，不重放历史工具副作用 |

普通前端不得传入 `model_client`、`environment`、timeout 或 `fsync`；这些仅是测试/受控嵌入注入点。旧 `build_local_backend` 可作为兼容 facade，但新同进程调用者使用 `build_in_process_adapter`。

### 3.2 Python binding

In-process Adapter 通过薄委托暴露 AgentBackend 的 Python binding：

```python
backend.send(command)
backend.events(since=cursor)
backend.last_state
backend.close()
```

- 正常 `send()` 返回表示命令已接受，不表示 turn 完成。
- 非法 Python 对象/字段抛 `TypeError` 或 `ValueError`；turn 进行中第二个任务抛 `TurnInProgressError`；关闭后命令抛 `BackendClosedError`。
- `events()` 返回长 `Iterator[EventEnvelope]`，停止迭代不等于 Interrupt/Close。
- `close()` 幂等。approval、worker shutdown 和 event poll 默认 timeout 分别为 120 s、30 s、0.1 s；测试可注入更小值。

### 3.3 In-process 恢复能力

`InProcessAdapter` 可额外公开 `resume_last_sequence` 和 `resume_diagnostics` 供 CLI 使用；它们不是 AgentBackend Port 的通用字段，也不自动进入 HTTP binding。未来其他 adapter 若支持跨进程 resume，必须在自己的公开接口中显式版本化该能力。

### 3.4 推荐消费循环

```python
cursor = int(getattr(backend, "resume_last_sequence", 0) or 0)
try:
    backend.send(SubmitTask(user_text))
    for event in backend.events(since=cursor):
        cursor = event.sequence
        render_defensively(event)
        if event.type == "approval_request":
            backend.send(ApprovalResponse(event.payload["request_id"], ask_user(event)))
        if event.type == "turn_end":
            break
finally:
    try:
        backend.send(CloseSession("frontend_exit"))
    except BackendClosedError:
        pass
    backend.close()
```

CLI 不需要阅读 HTTP 路由即可正确接入。

## 4. HTTP/SSE Adapter 接口

### 4.1 通用约定

- 基础路径 `/api/v1`；路径版本就是首版协议协商，不静默改变既有字段语义。
- JSON 使用 UTF-8。命令 `type`、字段名、状态值和事件 envelope 与语义规范逐字一致。
- 错误体为 `{"error":{"code":"stable_code","message":"safe message"}}`；客户端只按 code/status 分支。
- Agent HTTP 入口首版只监听 `127.0.0.1`，拒绝异常 Host/Origin，不提供通配 CORS 或公网 host。
- 模型、workspace、approval mode、session 输出目录和 API Key 在 Agent HTTP 进程启动时配置；普通浏览器请求不得提供或读取这些值。

### 4.2 Session 资源

HTTP adapter 使用独立 `transport_session_id` 定位其持有的 `AgentBackend`。该 ID 不得与 backend envelope 的 `session_id`、`agent_id`、`event_id`、`correlation_id` 或 provider ID 混用。

首版同时最多一个未关闭的 transport session；这是适配器并发边界，不改变 Agent 的线性 session 语义。浏览器失联不自动批准、拒绝、中断或关闭 Agent；显式 DELETE 或服务进程退出负责关闭。

### 4.3 端点

| 方法与路径 | 输入 | 成功 | 主要错误 |
| --- | --- | --- | --- |
| `GET /api/v1/health` | 无 | `200 {status:"ok",protocol_version:1}` | 503 启动不可用 |
| `POST /api/v1/sessions` | `{}` | `201 {transport_session_id,state,cursor}` | 409 已有活跃 session |
| `GET /api/v1/sessions/{id}` | 无 | `200 {state,cursor,closed}` | 404 未知、410 已关闭 |
| `GET /api/v1/sessions/{id}/events?since=n` | 非负整数；可带 `Last-Event-ID` | `200 text/event-stream` | 400 非法游标、404/410 session |
| `POST /api/v1/sessions/{id}/commands` | 四种 AgentCommand JSON | `202 {accepted:true}` | 400 非法命令、409 turn 进行中、410 backend 关闭 |
| `DELETE /api/v1/sessions/{id}` | 无 | `204`，幂等关闭并释放当前 backend | 404 未知 session |

### 4.4 SSE

每个 canonical event 编码为：

```text
id: <EventEnvelope.sequence>
event: agent-event
data: <完整 EventEnvelope JSON>
```

- 只发送 `sequence > cursor` 的事件；合法 `since` 和 `Last-Event-ID` 同时存在时使用较大值。
- keepalive 只能是 SSE 注释帧，不能制造 EventEnvelope。
- 客户端断开只结束该消费者；Agent 执行和 Store-first 持久化不受影响。
- POST 命令不得由 adapter 自动重放或排队；HTTP 连接失败不能据此断定命令未被接受。

### 4.5 命令、状态与错误映射

HTTP adapter 只接受：

- `SubmitTask(text)`
- `ApprovalResponse(request_id, approved)`
- `Interrupt(reason)`
- `CloseSession(reason)`

字段用现有构造器或等价共享验证器验证。`TurnInProgressError` 映射 409，`BackendClosedError` 映射 410，非法命令映射 400。未知内部异常映射安全化 500，响应和日志不得包含 traceback、任务正文、配置、provider 正文或 secret。

状态仍为 `RUNNING | WAITING_FOR_APPROVAL | COMPLETED_TURN | LIMIT_REACHED | INTERRUPTED | FAILED`。`turn_end` 是 turn 边界，`session_end`/流结束是 session 边界，`last_state` 只是派生快照。

## 5. Adapter Conformance 要求

In-process 与 HTTP 两种 adapter 必须复用同一组语义场景：

1. 四种命令的接受、拒绝和非阻塞语义；
2. sequence `>` 游标、重新 attach、慢消费者和关闭后的流结束；
3. approval request 先持久化，批准/拒绝/超时/中断/ID 不匹配 fail-closed；
4. turn 进行中第二个 SubmitTask 被拒绝而非排队；
5. tool failure 不等同 session failure，结束态与 follow-up 规则一致；
6. 未知/新增 payload 字段不改变 envelope 事实；
7. secret、异常和日志脱敏。

HTTP 测试可使用 fake backend 证明适配映射；只有对真实 `AgentBackendService` 的集成测试才能证明 HTTP Adapter、Port 与共享运行时正确接合。In-process Adapter 也必须对同一 service 工厂通过共享场景。这些证据都不能冒充真实模型网关或宿主 shell 隔离验证。

## 6. 前端依赖规则与变更控制

- Agent 后端规范是 Backend Service 与 adapter 实现依据，不是前端接入手册。CLI 只依赖 In-process Python binding 及本节共享规则；Web 只依赖 HTTP/SSE wire binding 及本节共享规则。
- In-process 与 HTTP Adapter 只能并列依赖 `AgentBackend` Port；HTTP 不得 import、构建或包装 `InProcessAdapter`。
- 有状态前端每成功处理一条事件后才能推进自己的 cursor；重复 sequence 幂等忽略，跳号或断线则从最后成功 cursor 重订阅。是否持久及持久介质由各 binding/产品规定。
- 前端对未知 event/payload/截断内容安全降级；最终文本优先使用 `turn_end.payload.assistant_text`，需要展示回退时才使用最近非空 assistant 文本。任何 tool/summary 字段都不构成前端执行副作用的授权。
- Adapter 可以暴露后端语义的等价 binding，但不得要求前端 import、调用或理解 AgentLoop、Runtime、Store、Environment、ModelClient、Registry 或 Policy。
- 删除/重命名命令或字段、改变游标比较、approval 关联、状态含义或结束边界，需要先改变语义规范并提升相应版本。
- 新增 transport 不得修改 Agent Loop、Tool、Policy、Environment 或 Session Store 来迁就某个前端。
- 静态资源托管属于 Web 产品组合，不属于 Agent HTTP adapter。任何 Vue/Vite 依赖进入 `transports/http/` 都视为边界违规。

## 7. 当前实现索引

| 主题 | 实现 |
| --- | --- |
| In-process Python binding | `src/coding_agent_neo/transports/in_process.py` (`InProcessAdapter`) |
| Shared backend factory seam | `src/coding_agent_neo/backend_factory.py` (`AgentBackendFactory`) |
| HTTP/SSE wire DTO、command decoder、ASGI app、session 生命周期 | `src/coding_agent_neo/transports/http/` |
| HTTP composition root | `src/coding_agent_neo/http_cli.py` (`coding-agent-neo-http`) |
| HTTP 客户端 binding 说明 | `docs/agent-http-client.md` |
