# CodingAgentNeo Agent 适配层接口规范

> 状态：In-process 与 HTTP/SSE binding 已实施；Web UI 作为独立产品消费第 4 节，不由本文定义
> 规范版本：1.1
> wire protocol：1；history DTO schema：1
> 日期：2026-09-02
> 后端依据：[agent-backend-interface.md](agent-backend-interface.md)
> 实施工作流：[backend-history-discover/](backend-history-discover/)

本文定义 Agent 侧各适配层向对应前端公开的接口，使不同类型前端能够平等使用同一套后端语义。Adapter 实现者以 `agent-backend-interface.md` 为内部依据；前端不得越过 adapter 直接依赖该后端规范：CLI 参考本文第 3 节 In-process binding 与第 6 节共享规则，Web 与其他进程外前端参考第 4 节 HTTP/SSE binding 与第 6 节共享规则。

**本文是所有 Agent 适配层公开 binding 的唯一权威规范。** README、运行示例、实现注释和测试可以链接本文，但不得另行定义或复制命令、wire、事件、状态、游标、授权、错误、安全或生命周期契约。Web 前端实现适配层接入时需要且只需要参考本文，不需要读取 `agent-backend-interface.md`、Python 源码或其他客户端说明文档；Web 产品布局、视觉和交付范围仍由对应产品架构与任务卡控制。

本文不改变 baseline 完成态；当前仓库已有 per-session In-process 和 live HTTP/SSE 行为。
workspace history provider、历史 DTO、有限 JSON 读取和 resume request 已由
`backend-history-discover/` 交付。Web 客户端只消费本文件第 4 节 binding，不直接依赖
Python 端口。Web 产品如何投影事件、选择 session 或展示授权对话框不属于本文契约。

## 1. 端口与适配器

```text
CLI ── In-process Adapter ──┐
                            ├── AgentBackend Port ── Backend Service/Runtime ── Agent Core
Web/other client ── HTTP/SSE Adapter ──┘
```

- `AgentBackend Port`：adapter 面向的后端应用端口，由独立的 Agent 后端接口规范定义。
- `Backend Service/Runtime`：`AgentBackend` 的共享具体实现，拥有 worker、事件缓冲、授权通道与 Core 组装；它不是 transport adapter。
- `In-process workspace binding`：绑定一个 `AgentBackendProvider`，在 session 选择前提供 history
  list/read 和 new/resume create；返回的 per-session binding 只是对 `AgentBackend` 的薄 Python
  binding，负责同进程生命周期和 CLI 需要的可选 resume 元数据，不拥有 Agent 执行语义。
- `HTTP/SSE Adapter`：Agent 侧、前端无关的网络适配器，把 history JSON、session JSON、command JSON 和
  live SSE 映射到一个 workspace-scoped `AgentBackendProvider`；provider 内部才负责由 assembly/factory
  创建 per-session `AgentBackend`。不得依赖 In-process Adapter、Vue、Vite、静态资源或具体前端。
- Web Client：只消费本文的 wire contract，不 import Python 源码，也不拥有 Agent 决策、安全和持久化。
- Workspace history binding：由 composition root 创建并绑定一个 resolved workspace；它只通过一个
  `AgentBackendProvider` 暴露 history/list/read/create。任何 binding 都不能再另行接收
  `SessionStore`、repository、factory、workspace path 或 session file path。

平等接入指语义一致，不要求所有前端使用同一种传输。CLI 可以保持同进程调用；进程外前端使用版本化 wire protocol。两种适配器必须共享 conformance suite。

## 2. 模块边界

| 模块 | 拥有 | 禁止拥有 |
| --- | --- | --- |
| `backend.py` | 公开 command/port/exception；前端无关语义 | worker、线程、队列、EventEmitter、SessionStore、AgentLoop、终端或 HTTP |
| `backend_service.py` | `AgentBackendService`、worker、EventStreamBuffer、ApprovalChannel/Port | CLI/HTTP I/O、Vue、transport session/route |
| `transports/in_process.py` | workspace-scoped In-process binding、返回的 per-session `InProcessAdapter`、同进程生命周期与 resume 元数据暴露 | worker、Agent Core 决策、CLI I/O、HTTP |
| `transports/http/` | HTTP app、provider-backed history/session wire DTO、SSE、session registry、错误映射、Host/Origin 防护 | Vue、Vite、`web/dist`、SessionStore、Agent Core 具体对象 |
| `assembly.py` | 组装 resolved workspace、`AgentBackendProvider` 与 per-session backend；在 composition root 组装具体 adapter | CLI/Web 展示和 HTTP 路由 |
| `http_cli.py` | Agent HTTP 服务启动与配置组合 | 静态 Web 资源和浏览器状态 |
| `web_launcher.py` | 可选地组合通用 HTTP app 与已构建静态资源 | 修改 transport/core 语义 |

当前 baseline 实现已将 `LocalAgentBackend` 的具体执行职责迁入 `AgentBackendService`，并以
`InProcessAdapter` 提供 per-session 薄 binding。T01 将 workspace-scoped binding 定为同进程正式入口；
T02–T05 已完成固定目录 provider、历史 DTO/读取、resume composition，以及 In-process 和 HTTP/SSE
binding。为避免破坏 baseline 用户和测试，`LocalAgentBackend`、
`build_local_backend` 以及旧的 `build_in_process_adapter` 名称仅保留为有测试、无行为分叉的兼容 facade；
兼容层不得成为 HTTP Adapter 的依赖。

## 3. In-process Adapter 接口

### 3.1 Canonical workspace-scoped composition

CLI 和其他受控 Python 同进程前端先通过 composition root 取得 workspace-scoped binding；此时尚未
选择或创建任何 session：

```python
from coding_agent_neo.assembly import build_in_process_workspace_binding

workspace = build_in_process_workspace_binding(config, interactive=True)
```

| 参数 | 类型 | 语义 |
| --- | --- | --- |
| `config` | `AppConfig` | 已解析、校验且只在 Agent 进程持有的配置；workspace 只在 composition root 解析。 |
| `interactive` | `bool` | 由之后 `create_session()` 创建的 per-session backend 是否允许事件授权。 |

Canonical workspace binding 的正式入口只有第 3.5 节的 `list_sessions()`、
`read_session_events()` 和 `create_session(resume_session_id=...)`；构造函数不接受 resume、session
path、`SessionStore`、repository、factory、model client 或 environment。历史读取在 session 选择前
即可完成；新 session/resume 必须显式调用 `create_session()`。

### 3.2 Per-session In-process binding

`create_session()` 返回的 per-session binding 通过薄委托暴露既有 `AgentBackend` Python binding：

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

### 3.3 Per-session 恢复元数据与兼容 facade

由 workspace binding 的 `create_session(resume_session_id=...)` 返回的 per-session binding 可额外公开
`resume_last_sequence` 和 `resume_diagnostics` 供 CLI 使用；它们不是 `AgentBackend` Port 的通用字段，
也不自动进入 HTTP binding。未来其他 adapter 若支持跨进程 resume，必须在自己的公开接口中显式版本化该能力。

已有名称 `build_in_process_adapter(config, interactive=True, resume=...)` 和
`build_local_backend(config, interactive=True, resume=...)` 仅作为兼容 facade 保留，不是 canonical
workspace binding，也不是 history/list/read 的正式入口。它们的 `resume` 仍只能是 opaque
`session_id` 字符串或 `None`，不得接受 `PathLike`、路径或 JSONL 文件名；内部必须先构造一个
workspace-scoped `AgentBackendProvider`/binding，再恰好调用一次 `create_session(resume_session_id=...)`。
兼容 facade 不得自行注入或访问第二个 persistence repository、`SessionStore`、backend factory、workspace
path、model client 或 environment；HTTP adapter 也不得依赖这些 facade。

### 3.4 推荐消费循环

```python
# Construct the workspace binding before selecting a session.  The value is
# None for a new session or an opaque ID returned by list_sessions().
workspace = build_in_process_workspace_binding(config, interactive=True)
resume_session_id: str | None = selected_history_id_or_none
backend = workspace.create_session(resume_session_id=resume_session_id)

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

需要显示历史时，CLI 先调用 `workspace.list_sessions(cursor=..., limit=...)`，再用
`workspace.read_session_events(session_id, since=..., limit=...)` 取得有限 page；二者都不创建 live
backend。CLI 不需要阅读 HTTP 路由即可正确接入，也不得把用户输入的路径传给任何一个方法。

### 3.5 Workspace history binding

以下是 In-process workspace binding 的唯一正式 Python 入口；受控 Python caller 通过 composition root
取得一个 workspace-scoped binding。binding 的唯一
后端依赖是 `AgentBackendProvider`；下面三个方法与 provider 语义等价，返回不可变的有限 DTO，
不暴露 `SessionStore`、repository、文件名或路径：

```python
class InProcessWorkspaceBinding(Protocol):
    def list_sessions(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> SessionHistoryPage: ...

    def read_session_events(
        self, session_id: str, *, since: int = 0, limit: int = 200
    ) -> SessionEventPage: ...

    def create_session(self, *, resume_session_id: str | None = None) -> InProcessAdapter: ...
```

`list_sessions()` 与 `read_session_events()` 在一次调用内完成并返回 page；它们不是长 iterator、
SSE 或后台任务。history page 的确切字段、排序、文本和事件上限以
`agent-backend-interface.md` 第 1.2 节为准：list `limit` 为 `1..100`（默认 50），event `since`
为 `0..2**63-1`、`limit` 为 `1..200`（默认 200）。调用者必须使用不透明 list cursor 原样翻页，
并把 event page 的 `next_cursor` sequence 作为下一次 `since`；不得自行读取或拼接路径。

In-process 的错误保留 provider 的 typed stable errors（`invalid_history_id`、
`invalid_history_cursor`、`invalid_history_limit`、`history_not_found`、`history_unavailable` 和
`invalid_resume`），message 仍为安全固定短句。`create_session({})` 的 Python 等价是
`resume_session_id=None`；resume 只接受非空 opaque `session_id` 字符串。返回的 per-session
binding 继续提供 `send/events/last_state/close`，resume 时可提供
`resume_last_sequence`（恢复前最后 sequence）和 `resume_diagnostics`；二者不改变
`AgentBackend` Port，也不包含路径。

In-process 与 HTTP 必须保持下表的可观察等价性：

| 语义 | In-process binding | HTTP binding |
| --- | --- | --- |
| 列表 | 返回 `SessionHistoryPage` | `200 application/json`，body 即同一字段结构 |
| 历史读取 | 返回 `SessionEventPage` | `200 application/json`，一次有限响应，不是 SSE |
| 新 session | `create_session(resume_session_id=None)` | `POST /api/v1/sessions` 空 body 或 `{}` |
| resume | `create_session(resume_session_id="session_...")` | `POST /api/v1/sessions` body 只含 `resume_session_id` |
| 不安全输入 | provider typed error | 对应 stable `error.code`/HTTP status |
| live session | per-session `send/events/last_state/close` | 既有 command/status/SSE/DELETE routes |

历史读取不创建 transport session、不执行 Agent command、不 replay 工具副作用。resume 在两个
binding 中都重新验证 fixed-directory 文件；listing 的 `resumable` 或 cursor 不是创建时的授权。

## 4. HTTP/SSE Adapter 接口

### 4.1 通用约定

- 基础路径 `/api/v1`；路径版本就是首版协议协商，不静默改变既有字段语义。
- JSON 使用 UTF-8。命令 `type`、字段名、状态值和事件 envelope 与语义规范逐字一致。
- 错误体为 `{"error":{"code":"stable_code","message":"safe message"}}`；客户端只按 code/status 分支。
- Agent HTTP 入口为 `coding-agent-neo-http`，首版固定监听 `127.0.0.1`，默认端口 `8765`；可用 `--port` 改变本地端口，但不提供 `--host`、通配 CORS 或公网监听。
- 模型、workspace、approval mode 和 API Key 在 Agent HTTP 进程启动时配置；session 输出目录始终由
  `resolved_workspace / ".coding-agent-neo" / "sessions"` 派生，既不是配置字段也不是请求参数。
  普通浏览器请求不得提供或读取 workspace、目录、文件名、模型或凭据。
- 生产配置和 CLI 不提供 `session_dir` 或 `--session-dir`；旧输入必须按 unknown-option/config
  validation 拒绝。CLI 的既有 `--resume SESSION_ID` 只接受 opaque ID，不接受 JSONL 路径；custom
  session directories 不自动发现或迁移。
- 独立 Agent HTTP 入口只提供 `/api/v1`，不 import、定位或托管 Vue、Vite、`web/dist` 等静态资源。

安装与启动入口属于本 binding 的部署契约：

```bash
python -m pip install -e ".[dev,http]"
coding-agent-neo-http --config .coding-agent-neo.toml
```

`--api-key-env` 只能指定环境变量名，不能传 API Key 值。扩大网络暴露或让浏览器携带配置、workspace、模型信息或凭据，均属于新的安全契约，不能由前端自行约定。

Host/Origin 安全中间件覆盖所有 `/api/v1` 路由，包括 health、history list/read、session create/status、
commands、SSE 和 DELETE；路由不得提供绕过校验的旁路。默认允许的 Host hostname 只有
`127.0.0.1`、`localhost` 和测试 sentinel `testserver`（可带合法 port，不能含 userinfo、path、query 或
fragment）。Origin 缺省时允许；如果出现 Origin，只允许 `http`/`https`、hostname 为上述三个本地值、可带
合法 port 且 path 为空或 `/` 的来源；`null`、通配符和其他 hostname 一律拒绝。拒绝分别返回
`400 invalid_host` 或 `400 invalid_origin`，message 不含请求值。

### 4.2 Workspace history resources

History API 使用与 In-process provider 等价的 DTO；响应是一次有限 JSON，不建立 SSE 流、不发送
keepalive、不返回 raw JSONL，也不创建或改变 transport session。所有 query/path/body 在调用 provider
前完成类型、长度和安全 ID 校验。

#### 4.2.1 `GET /api/v1/session-history`

Query 参数：

| 参数 | 类型/默认 | 约束 |
| --- | --- | --- |
| `limit` | ASCII 十进制 integer；默认 `50` | `1..100`；boolean、空值、符号、超界或重复参数均为 invalid。 |
| `cursor` | opaque ASCII string；默认缺省（第一页） | 只接受 provider 返回的 token，最多 256 字符；不得解码为 offset、session ID 或路径。 |

不得带 request body 或 `path`、`filename`、`session_dir` 等替代参数。成功响应为 `200`，body 是
UTF-8 JSON（`Content-Type: application/json`；charset 参数若出现不改变契约），字段精确为：

```json
{
  "sessions": [
    {
      "session_id": "session_0123456789abcdef0123456789abcdef",
      "first_user_message": {
        "text": "请检查失败测试",
        "truncated": false,
        "original_length": 21,
        "limit": 4096,
        "encoding": "utf-8"
      },
      "created_at": "2026-09-01T08:00:00.000000Z",
      "updated_at": "2026-09-01T08:01:00.000000Z",
      "last_sequence": 7,
      "last_state": "COMPLETED_TURN",
      "resumable": true,
      "diagnostics": []
    }
  ],
  "next_cursor": null
}
```

`sessions` 最多 100 项，按 `(updated_at, session_id)` 降序排序；`updated_at=null` 排在有效时间之后，
再按 ID 降序稳定打破平局。`first_user_message` 最多 4,096 UTF-8 字节，并按 backend DTO 的
`BoundedText` 结构明确标记截断。每个 item 的 `diagnostics` 最多 8 项，单个坏 candidate 只能变成
safe diagnostic，不能使健康 item 消失或使整个列表失败。`next_cursor` 无下一页为 `null`，否则为最多
256 ASCII 字符的 opaque provider token；客户端必须原样回送。

#### 4.2.2 `GET /api/v1/session-history/{session_id}/events`

`session_id` 是路径中的 opaque ID，不是路径参数；它必须在 path construction 前通过
`session_...` 安全 token 校验。`/`、`\\`、`.` suffix、NUL、控制字符、绝对/相对路径、`.jsonl` 文件名和
directory component 均非法。Query 参数如下：

| 参数 | 类型/默认 | 约束 |
| --- | --- | --- |
| `since` | JSON/ASCII 十进制 integer；默认 `0` | `0..2**63-1`；boolean、符号、空值或重复参数均为 invalid。只返回 `sequence > since`。 |
| `limit` | ASCII 十进制 integer；默认 `200` | `1..200`；boolean、空值、符号、超界或重复参数均为 invalid。 |

`Last-Event-ID` 只属于 live SSE route，不参与 finite history read。不得带 body、path、filename 或任意
文件定位参数。成功响应为一次 `200` UTF-8 JSON 响应（`Content-Type: application/json`；charset
参数若出现不改变契约），字段精确为：

```json
{
  "session_id": "session_0123456789abcdef0123456789abcdef",
  "events": [],
  "next_cursor": null,
  "has_more": false,
  "diagnostics": []
}
```

`events` 按 canonical `sequence` 升序，最多 `limit` 项；每个 envelope 保持后端 schema、sequence、
identity 和 payload 业务语义。历史 projection 对 payload 适用每事件 65,536 字节、每 page 8 MiB 的
上限，超出部分替换为标准 `{truncated, original_length, limit, encoding, head, tail}` preview object，
不得截断 session/event/agent ID 或 sequence。`next_cursor` 在 `has_more=true` 时是本页最后 sequence
的 JSON integer，否则为 `null`；空页也必须 `has_more=false` 且 `next_cursor=null`。客户端可把该整数
作为下一次 `since`，但它不是 list cursor，也不是自动 replay/重新执行命令的凭据。

History list/read 的错误 body 一律是 `{"error":{"code":"stable_code","message":"safe message"}}`；
不得包含 path、workspace、原始 JSONL、用户正文、traceback、配置、secret 或 provider exception text。
历史读取即使连接仍保持也必须在一个有限响应后结束；只有 `/sessions/{id}/events` live route 使用 SSE。

### 4.2.3 Session 资源

HTTP adapter 使用独立 `transport_session_id` 定位其持有的 `AgentBackend`。该 ID 不得与 backend envelope 的 `session_id`、`agent_id`、`event_id`、`correlation_id` 或 provider ID 混用。

首版同时最多一个未关闭的 transport session（single-active-session rule）；这是适配器并发边界，不改变
Agent 的线性 session 语义。浏览器失联不自动批准、拒绝、中断或关闭 Agent；显式 DELETE 或服务进程退出负责关闭。

### 4.3 端点

| 方法与路径 | 输入 | 成功 | 主要错误 |
| --- | --- | --- | --- |
| `GET /api/v1/health` | 无 | `200 {status:"ok",protocol_version:1}` | — |
| `GET /api/v1/session-history?limit=n&cursor=...` | bounded list query | `200 SessionHistoryPage`（finite JSON） | 400 invalid ID/cursor/limit、404/422 history |
| `GET /api/v1/session-history/{session_id}/events?since=n&limit=m` | opaque ID + bounded query | `200 SessionEventPage`（finite JSON） | 400 invalid ID/cursor/limit、404/422 history |
| `POST /api/v1/sessions` | 空 body、`{}` 或 `{"resume_session_id":"session_..."}` | `201 {transport_session_id,state,cursor}` | 400 非法 body/ID、409 已有活跃 session、422 invalid resume |
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

#### 4.5.1 Session creation and resume

`POST /api/v1/sessions` 的 body 只能是以下三种等价新建/恢复请求：

```json
{}
```

```json
{"resume_session_id":"session_0123456789abcdef0123456789abcdef"}
```

或者零字节空 body；空 body 与 `{}` 都表示新 session。带 resume 时 object 必须恰好只有
`resume_session_id` 一个 key，其值必须是非空 opaque `session_...` 字符串；`null`、数组、字符串、
额外 key、`path`、`filename`、`session_dir`、绝对/相对路径和 `.jsonl` 文件名均拒绝。校验和单 active
registry 检查先于 backend/provider 创建，因此 active session 存在时返回 `409 session_exists`，不得
为第二个请求构造或打开 backend。

成功响应字段始终精确为：

```json
{"transport_session_id":"transport_...","state":"RUNNING","cursor":0}
```

新 session 的 `cursor` 为 `0`；resume 的 `cursor` 为 provider 重验证后恢复文件的最后 canonical
sequence。`transport_session_id` 只定位 adapter 所持有的 backend，不能当作 backend `session_id`、
history ID、event ID 或 provider cursor。成功创建后，客户端可以从返回 cursor 开始订阅 live SSE；
resume 后首次 follow-up 仍会继续原 session sequence，且可以产生新的 `session_start`/`agent_start`，
不代表历史事件被 replay。

#### 4.5.2 Command mapping

HTTP adapter 只接受：

- `{"type":"SubmitTask","text":"检查失败测试"}`
- `{"type":"ApprovalResponse","request_id":"correlation_...","approved":true}`
- `{"type":"Interrupt","reason":"user_cancelled"}`
- `{"type":"CloseSession","reason":"frontend_exit"}`

`SubmitTask.text`、`ApprovalResponse.request_id` 和显式提供的 `reason` 必须是非空字符串；`approved` 必须是 JSON boolean。命令不接受额外字段。成功的 `202 {"accepted":true}` 只表示命令已被接受，不表示 turn 已完成；POST 连接失败也不能证明命令未被接受，因此客户端不得自动重放。

稳定错误映射如下；`message` 必须使用表中的固定安全短句，客户端逻辑只按 HTTP status 和 `code` 分支：

| HTTP | `error.code` | 条件 | 固定 `message` |
| --- | --- | --- | --- |
| 400 | `invalid_host` | Host 不是允许的本地值 | `request host is not allowed` |
| 400 | `invalid_origin` | Origin 不是允许的本地来源 | `request origin is not allowed` |
| 400 | `invalid_session_request` | 创建 session 的 body 既非空也不是空 JSON object，或 JSON 非法 | `session request is invalid` |
| 400 | `invalid_history_id` | history path ID 或 `resume_session_id` 不是合法 opaque ID，或包含路径成分 | `history session ID is invalid` |
| 400 | `invalid_history_cursor` | history list cursor 或 finite event `since` 无效（SSE 的 live cursor 仍使用 `invalid_cursor`） | `history cursor is invalid` |
| 400 | `invalid_history_limit` | history list/read 的 `limit` 缺失格式、重复、非整数或超出 `1..100`/`1..200` | `history limit is invalid` |
| 400 | `invalid_cursor` | `since` 或 `Last-Event-ID` 不是非负整数 | `event cursor is invalid` |
| 400 | `invalid_command` | JSON、命令类型、字段、字段类型或字段值非法 | `command is invalid` |
| 404 | `session_not_found` | transport session ID 未知 | `transport session was not found` |
| 404 | `history_not_found` | 合法 history ID 在 fixed directory 当前没有对应 session | `session history was not found` |
| 409 | `session_exists` | 已有一个未关闭的 transport session | `an active transport session already exists` |
| 409 | `turn_in_progress` | turn 执行中再次提交 `SubmitTask` | `a turn is already running` |
| 422 | `history_unavailable` | 对应 JSONL 存在但读取、schema、identity 或 sequence 校验失败 | `session history is unavailable` |
| 422 | `invalid_resume` | 对应文件当前无法恢复（缺 root/context/budget 或重验证失败） | `session cannot be resumed` |
| 410 | `session_closed` | transport session 或 backend 已关闭 | `transport session is closed` |
| 500 | `internal_error` | 未知内部错误，正文已安全化 | `the Agent service could not complete the request` |

未知内部异常映射安全化 500；响应和日志不得包含 traceback、任务正文、配置、provider 正文或 secret。

状态仍为 `RUNNING | WAITING_FOR_APPROVAL | COMPLETED_TURN | LIMIT_REACHED | INTERRUPTED | FAILED`。`turn_end` 是 turn 边界，`session_end`/流结束是 session 边界，`last_state` 只是派生快照。

| 状态 | 客户端语义 | 是否可提交 follow-up |
| --- | --- | --- |
| `RUNNING` | turn 正在运行，或 session 初始尚无 turn；不能仅凭该快照判断命令是否完成 | 仅在没有活跃 turn 时可提交 |
| `WAITING_FOR_APPROVAL` | 等待唯一授权响应 | 否；只能授权、拒绝、中断或关闭 |
| `COMPLETED_TURN` | 最近 turn 正常完成，session 仍打开 | 是 |
| `LIMIT_REACHED` | 达到预算或上下文限制 | 否，终止态 |
| `INTERRUPTED` | 用户/adapter 已取消 | 否，终止态 |
| `FAILED` | 不可恢复系统错误 | 否，终止态 |

### 4.6 HTTP EventEnvelope v1 wire schema

SSE `data` 必须是以下完整 JSON object；adapter 只序列化 canonical envelope，不重命名字段、不重新编号 sequence，也不改写 payload 业务含义：

```json
{
  "schema_version": 1,
  "session_id": "session_...",
  "event_id": "event_...",
  "agent_id": "agent_...",
  "parent_agent_id": null,
  "sequence": 7,
  "type": "assistant_message",
  "correlation_id": null,
  "provider_tool_call_id": null,
  "timestamp": "2026-08-31T08:00:00.123456Z",
  "payload": {}
}
```

| 字段 | v1 wire 约束 |
| --- | --- |
| `schema_version` | 当前固定为整数 `1` |
| `session_id` | Agent 线性 session ID；不得与 transport session ID 混用 |
| `event_id` | Agent session 内唯一事件 ID |
| `agent_id` | 事件所属 Agent；首版只有 root Agent |
| `parent_agent_id` | root Agent 为 `null`；保留字段不代表支持子 Agent |
| `sequence` | 从 1 开始严格递增且不重复；客户端游标比较使用 `>` |
| `type` | 第 4.7 节事件名；未知值必须安全保留或忽略 |
| `correlation_id` | 工具生命周期关联 ID，否则通常为 `null` |
| `provider_tool_call_id` | 模型供应方不透明 ID；不得与 correlation ID 混用 |
| `timestamp` | UTC ISO 8601，使用 `Z` 后缀 |
| `payload` | 始终为 JSON object；允许未知字段、缺失业务字段或整体截断 |

若 canonical payload 超过后端 session 输出限制，整个 `payload` 可替换为以下预览 object；客户端必须降级展示，不能继续假定原事件业务字段存在：

```json
{
  "truncated": true,
  "original_length": 123456,
  "limit": 1000000,
  "encoding": "utf-8",
  "head": "...",
  "tail": "..."
}
```

### 4.7 HTTP 公开事件目录

下表是前端可消费的 schema v1 事件。`?` 表示字段可以缺省或为 `null`；客户端只读取所需字段并忽略新增字段。任何事件内容都只是展示和状态事实，不能由前端重新解析为待执行操作。

| `type` | payload v1 | 前端消费语义 |
| --- | --- | --- |
| `session_start` | `state` | Agent session 开始 |
| `agent_start` | `state`, `active_tools[]` | Agent 开始及可用工具展示 |
| `user_message` | `text` | 本 turn 用户输入事实 |
| `assistant_message` | `text`, `finish_reason?`, `usage?`, `diagnostics[]`, `tool_calls[]` | 一次完整模型响应；不是 token streaming |
| `tool_call` | `tool_name`, `name`, `argument_keys[]`, `arguments`, `arguments_length?` | 工具生命周期开始；参数值已脱敏，不得由前端执行 |
| `approval_request` | `request_id`, `tool_name`, `arguments_summary`, `timeout_seconds` | 唯一待处理授权请求 |
| `policy_decision` | `tool_name`, `name`, `requested`, `requested_decision`, `decision`, `action`, `approved?`, `reason` | 最终 allow/deny 审计事实 |
| `tool_result` | `tool_name`, `name`, `status`, `text`, `truncated`, `original_length?`, `result`, `tool_result` | 工具结果；非 success 不等同整个 session FAILED |
| `compaction` | `status`, `forced`, `source_start_sequence`, `source_end_sequence`, `covered_through_sequence`, `degraded_through_sequence?`, `summary?`, `error_type?`, `reason?` | 上下文压缩或退化 |
| `retry` | `reason` 及传输重试或 context overflow 对应字段 | 可恢复重试；不能假定所有 retry 都有 `delay_seconds` 或 `category` |
| `turn_end` | `state`, `reason`, `limit_reason?`, `assistant_text`, `budget` | **唯一 turn 完成边界**；最终文本优先取 `assistant_text` |
| `error` | `state`, `recoverable?`, `reason?`, `diagnostics[]?`, `error_type?`, `message?`, `stack[]?` | 过程协议错误或安全化系统错误；最终状态仍以后续结束事件为准 |
| `agent_end` | `state`, `reason`, `budget` | Agent 生命周期结束 |
| `session_end` | `state`, `reason`, `budget` | session 生命周期结束 |

授权请求必须满足 `payload.request_id == envelope.correlation_id`，且 request ID 为非空字符串。前端批准或拒绝时只能原样回送该 ID；不得从 `arguments_summary`、tool 参数或展示文本猜测/重建。`tool_name` 标识待授权工具，不限于 `bash`；前端必须按任意工具名展示同一套授权交互。Escape、断线、超时、关闭和 ID 不匹配均不得产生批准效果。

`assistant_message.tool_calls[]` 的公开字段为 `correlation_id`、`provider_tool_call_id`、`name`、`raw_arguments` 和 `diagnostics[]`。`raw_arguments` 是脱敏后的不可信 JSON 文本，不保证可以解析；无效工具名可投影为 `<invalid-tool-name>`。

`tool_result.result`（及兼容别名 `tool_result`）公开 `correlation_id`、`provider_tool_call_id`、`status`、`text`、`metadata`、`truncated`、`original_length?`、`duration_seconds`、`exit_code?`、`timed_out` 和 `path?`。`status` 的公开值为 `success | error | denied | invalid | cancelled | timeout`。

模型传输 retry 可包含 `reason`、`category`、`status_code?`、`attempt`、`max_attempts` 和 `delay_seconds`；context overflow retry 包含 `reason=context_overflow`、`forced_compaction=true`、`attempt` 和 `max_attempts`。`assistant_message.tool_calls[].raw_arguments`、`tool_call.arguments` 和 `approval_request.arguments_summary` 均是不可信展示内容，不构成浏览器执行副作用的授权。

### 4.8 事件顺序与 session 生命周期

典型成功路径为：

```text
首次 turn: session_start → agent_start
每个 turn: user_message
           → assistant_message
           → [tool_call → (approval_request)? → policy_decision → tool_result] × N
           → [assistant_message → ...] × N
           → turn_end(COMPLETED_TURN)
显式关闭: agent_end → session_end → SSE 结束
```

- 同一次模型响应声明的多个工具严格串行；每个工具调用恰好产生一个 `tool_result`。
- 成功 `turn_end(COMPLETED_TURN)` 不自动关闭 transport/Agent session；客户端可以在同一 session 发送线性 follow-up。
- `LIMIT_REACHED`、`INTERRUPTED` 和 `FAILED` 是终止态，不能通过前端重新开放；后端会尽最大努力追加 `agent_end` 和 `session_end`。
- SSE 结束或 `session_end` 才是 session 生命周期边界；浏览器断线本身不是结束、拒绝、批准或中断证据。
- GET/SSE 可以从最后成功处理的 cursor 有界重连；任何 POST 命令都不得自动重放或排队。

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

- Agent 后端规范是 Backend Service 与 adapter 实现依据，不是前端接入手册。CLI 只依赖本文 In-process Python binding 及本节共享规则；Web 只依赖本文 HTTP/SSE wire binding、公开事件目录及本节共享规则。前端实现不得把 README、Python 源码或其他说明文件当作补充契约。
- In-process 与 HTTP workspace binding 只能依赖 `AgentBackendProvider`；各自返回的 per-session binding
  再依赖 `AgentBackend` Port。HTTP 不得 import、构建或包装 `InProcessAdapter`，也不得绕过 provider
  直接依赖 `SessionStore`。
- 有状态前端每成功处理一条事件后才能推进自己的 cursor；重复 sequence 幂等忽略，跳号或断线则从最后成功 cursor 重订阅。是否持久及持久介质由各 binding/产品规定。
- 前端对未知 event/payload/截断内容安全降级；最终文本优先使用 `turn_end.payload.assistant_text`，需要展示回退时才使用最近非空 assistant 文本。任何 tool/summary 字段都不构成前端执行副作用的授权。
- Adapter 可以暴露后端语义的等价 binding，但不得要求前端 import、调用或理解 AgentLoop、Runtime、Store、Environment、ModelClient、Registry 或 Policy。
- 删除/重命名命令或字段、改变游标比较、approval 关联、状态含义或结束边界，需要先改变语义规范并提升相应版本。
- 新增 transport 不得修改 Agent Loop、Tool、Policy、Environment 或 Session Store 来迁就某个前端。
- 静态资源托管属于 Web 产品组合，不属于 Agent HTTP adapter。任何 Vue/Vite 依赖进入 `transports/http/` 都视为边界违规。

## 7. 实现与契约索引

| 主题 | 实现/契约状态 |
| --- | --- |
| In-process Python binding | `src/coding_agent_neo/transports/in_process.py` (`InProcessWorkspaceBinding`, `InProcessAdapter`) |
| Workspace provider and fixed-path composition | `src/coding_agent_neo/assembly.py`, `src/coding_agent_neo/backend_provider.py`, `src/coding_agent_neo/session.py` |
| HTTP/SSE wire DTO、command decoder、ASGI app、session 生命周期 | `src/coding_agent_neo/transports/http/` |
| HTTP composition root | `src/coding_agent_neo/http_cli.py` (`coding-agent-neo-http`) |
| Workspace history provider/DTO、finite history routes and resume request | Implemented in `src/coding_agent_neo/backend.py`, `src/coding_agent_neo/backend_provider.py`, and `src/coding_agent_neo/transports/http/`; contract in this document and `agent-backend-interface.md` |
| 前端接入唯一规范 | 本文第 3 节（In-process）、第 4 节（HTTP/SSE）和第 6 节（共享规则） |
