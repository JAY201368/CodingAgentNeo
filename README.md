# CodingAgentNeo

CodingAgentNeo 是一个使用 Python 3.12 从零实现的本地编程智能体。它通过 OpenAI-compatible 原生 tool calling 接口驱动模型，自主浏览、搜索和修改工作区，执行命令与测试，并把工具结果继续反馈给模型，直到完成当前编程任务。

项目没有使用 LangChain、OpenAI Agents SDK 等 Agent 框架，也不依赖服务端托管的文件或代码执行工具。Agent Loop、工具协议、权限策略、执行环境、上下文压缩、事件轨迹、会话恢复以及前后端适配均由项目自行实现。

## 功能概览

- 完整的 `LLM → Tool Calls → Tool Results → LLM` 编程闭环，支持一次模型响应中的多个工具调用，并按声明顺序串行执行。
- 六个内置工作区工具：`read_file`、`list_files`、`search`、`write_file`、`edit_file` 和 `bash`。
- 交互式 CLI、单次非交互式 CLI、独立 HTTP/SSE API 和 Vue Web GUI。
- `ask`、`auto`、`deny` 三种执行权限模式；读取工具默认允许，写文件和命令执行受策略控制。
- append-only JSONL 事件轨迹、稳定 correlation ID、运行预算、错误重试与完整工具生命周期记录。
- 自动增量上下文压缩；持久化历史始终保留，模型工作上下文仅保留当前所需投影。
- 工作区历史会话分页、有限事件读取和线性恢复；恢复不会重放历史工具副作用。
- 文件操作的工作区边界与符号链接校验、输出截断、凭据和事件脱敏。
- 注册与激活分离的统一 Tool 协议，以及可替换的 `ExecutionEnvironment` 执行边界。

## 环境要求

- Python `>=3.12,<3.13`
- 使用 Web GUI 时：Node.js 20+、npm 10+
- 一个支持原生 function/tool calling 的 OpenAI-compatible 模型服务

## 快速开始

### 1. 安装

仅运行 CLI：

```bash
python -m pip install -e .
```

运行完整项目或参与开发：

```bash
python -m pip install -e ".[dev,http]"
npm --prefix web ci
```

### 2. 配置

复制示例配置：

```bash
cp config.example.toml .coding-agent-neo.toml
```

至少设置模型，并把 API Key 放入 `api_key_env` 所指定的环境变量：

```toml
model = "your-model-name"
api_base = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
workspace = "."
approval_mode = "ask"
```

```bash
export OPENAI_API_KEY="..."
```

`.coding-agent-neo.toml` 已被忽略。`api_key_env` 保存的是环境变量名，不是密钥值；项目不提供 `--api-key` 参数。不要把密钥写入源码、命令行参数、受版本控制的配置、会话文件或演示材料。

配置覆盖顺序从高到低为：

```text
CLI 参数 > CODING_AGENT_NEO_* 环境变量 > 本地 TOML > 默认值
```

默认读取当前目录的 `.coding-agent-neo.toml`，也可使用 `--config PATH` 或 `CODING_AGENT_NEO_CONFIG` 指定文件。完整字段与默认值见 [`config.example.toml`](config.example.toml)。

### 3. 运行 CLI

交互式运行：

```bash
coding-agent-neo
```

也可以直接使用模块入口：

```bash
python -m coding_agent_neo
```

单次任务：

```bash
coding-agent-neo --task "检查失败测试并修复问题"
printf '%s\n' "总结这个项目的测试结构" | coding-agent-neo
```

恢复已有会话：

```bash
coding-agent-neo --resume session_0123456789abcdef0123456789abcdef
```

交互式会话在一个 turn 完成后可继续输入 follow-up。输入 `/permissions` 查看或选择权限模式，也可直接使用 `/permissions ask`、`/permissions auto` 或 `/permissions deny`。按 Ctrl+C 会尽力中断运行并保存已产生的轨迹。

非交互式 `ask` 模式不会读取 stdin 进行授权，而是立即拒绝写操作和 `bash`。只有明确接受其风险时，才为无人值守任务使用：

```bash
coding-agent-neo --approval-mode auto --task "修复测试并运行验证"
# 等价简写
coding-agent-neo --yolo --task "修复测试并运行验证"
```

单次模式把最终 assistant 文本写到 stdout，把事件与状态诊断写到 stderr。退出码如下：

| 退出码 | 含义 |
| --- | --- |
| `0` | 当前 turn 正常完成 |
| `1` | 启动或运行失败（`FAILED`） |
| `2` | 命令用法或配置错误 |
| `3` | 达到执行限制（`LIMIT_REACHED`） |
| `130` | 用户中断（`INTERRUPTED`） |

会话开始执行后，轨迹固定写入 `<workspace>/.coding-agent-neo/sessions/<session_id>.jsonl`。CLI 的 `--resume` 只接受不透明 session ID，不接受路径或文件名。

## Web GUI

### 一体化本地运行

先构建前端，再启动同源 Web/API 组合服务：

```bash
npm --prefix web run build
coding-agent-neo-web --config .coding-agent-neo.toml
```

浏览器访问 [http://127.0.0.1:8765](http://127.0.0.1:8765)。若前端产物不在仓库的 `web/dist`，使用 `--dist-dir PATH` 指定外部构建目录。

Web GUI 提供任务提交、事件过程折叠、工具结果、授权对话框、follow-up、历史分页和历史恢复。打开页面时只加载历史列表，不会自动创建或附着会话，右侧工作区保持空白；使用侧边栏圆形按钮新建会话，或选择一个可恢复历史会话。

Web 生命周期约定：opening the Web GUI **only loads the history list** and **does not automatically create or attach** a transport session. The **circular sidebar** button creates a session; selecting a resumable **history item** first `DELETE`s the known transport (including a **persisted transport-ID hint**) and then sends exactly one `POST` with `{"resume_session_id":"session_..."}`. Historical `session_end` events affect only the **history projection** and never close the new live transport. History is hydrated through **finite JSON**, not replayed by live SSE. The sidebar and conversation pane **scroll independently**. The main pane **has no End Session, reconnect, or new-session buttons**. HTTP permits **one active transport session**; after a failed replacement, the client stays fail-closed and **does not recreate a session automatically**.

### 前后端分离开发

终端一启动仅提供 `/api/v1` 的 Agent HTTP/SSE 服务：

```bash
coding-agent-neo-http --config .coding-agent-neo.toml
```

终端二启动 Vite；开发服务器只把 `/api` 代理到上述回环服务：

```bash
npm --prefix web run dev
```

`coding-agent-neo-http` 和 `coding-agent-neo-web` 默认只监听 `127.0.0.1:8765`。前者不托管 Web 资源；后者只是把同一个前端无关 HTTP Adapter 与已经构建的静态资源组合起来，不是第二套 API 实现。

## 关键设计

```text
CLI ── In-process Adapter ──┐
                            ├── AgentBackend Port ── Backend Service
Web ── HTTP/SSE Adapter ────┘                         │
                                                      ▼
                         Agent Loop ── AgentRuntime / Context / Budget
                              │
                              ├── Model Client
                              ├── Tool Registry ── Tool Executor ── Policy
                              │                                      │
                              │                         ExecutionEnvironment
                              └── Event Emitter ── append-only Session Store
```

### 自研线性 Agent Loop

每个 turn 先持久化用户消息，再从历史投影模型上下文。模型产生工具调用时，系统完成参数校验、策略判定、授权、执行和结果记录，然后继续调用模型；模型返回无工具调用的非空文本时结束当前 turn。单个工具失败会形成结构化结果交还模型，而不是直接让整个会话崩溃。

每个模型声明的工具调用都必须恰好闭合为一个 `tool_result`，包括成功、失败、拒绝、参数非法、取消和超时。内部 correlation ID 贯通调用、授权与结果；模型供应方的 tool-call ID 独立保留，二者不混用。

### Tool、Policy、Environment 分层

- `Tool` 只声明名称、描述、参数 schema，并将请求和响应翻译为统一 `ToolResult`。
- `ToolRegistry` 区分 registered 与 active；只有激活工具的 schema 会暴露给模型。
- `ExecutionPolicy` 在副作用发生前作 `allow | ask | deny` 决策，未知工具、非法参数和策略异常均 fail-closed。
- `ExecutionEnvironment` 是文件、搜索和进程能力的唯一入口。内置工具不直接访问宿主文件系统或 `subprocess`。

生产环境使用 `LocalExecutionEnvironment`。结构化文件工具只能处理工作区内的相对路径，并检查路径穿越与符号链接逃逸；`bash` 只保证初始工作目录位于 workspace，它继承启动用户的文件系统、网络和进程权限，**不是操作系统沙箱**。

### 持久化历史与工作上下文分离

Session Store 以 append-only JSONL 保存规范事件，事件先落盘再交给前端消费。模型上下文是该历史的可重建投影，并在接近预算时增量压缩：system prompt 始终保留，tool call 与对应 tool result 作为不可拆分组处理。压缩不会改写或删除原始会话事实。

恢复会话时，系统重新校验事件身份、严格递增 sequence、工具集合、上下文和预算，在同一线性 session 上继续编号；已经执行过的工具不会再次运行。文件末尾仅有一条不完整 JSON 记录时可诊断并忽略，其余结构损坏则拒绝恢复。

### 统一后端与双适配器

CLI 与 Web 共用同一个 `AgentBackend` 应用端口和 Backend Service：CLI 使用同进程 Adapter，Web 使用版本化 HTTP/SSE Adapter。适配层只映射命令、事件、游标和错误，不持有 Agent Loop、Runtime、Store、Environment、Model Client 或 Tool Registry。

历史列表和历史事件采用有界、有限的 JSON 响应；只有活动会话事件使用 SSE。浏览器断线不会自动批准、拒绝、中断或关闭 Agent。GET/SSE 可以从最后成功处理的游标重连，POST 命令绝不自动重放。

### Runtime 隔离与有界执行

每个 Agent 的身份、上下文状态、预算、active tools、取消信号、策略和环境引用均属于独立 `AgentRuntime`，核心循环不依赖进程级可变全局状态。运行受模型步数、工具调用数、墙钟时间、命令超时、上下文窗口和输出长度限制；达到限制会生成明确的终止状态和预算快照。

## 关键规范与契约

README 只说明如何使用和理解项目，不重复定义协议。修改实现或新增接入时，以下版本化文档是权威来源：

| 文档 | 当前版本 | 约束范围 |
| --- | --- | --- |
| [Agent 后端接口规范](docs/agent-backend-interface.md) | Backend `1.3`、Event Schema `1`、History DTO `1` | `AgentBackendProvider`、`AgentBackend`、五种命令、状态机、事件信封、历史 DTO、授权和生命周期 |
| [Agent 适配层接口规范](docs/agent-transport-interface.md) | Adapter `1.2`、Wire Protocol `1`、History DTO `1` | In-process binding、HTTP `/api/v1`、SSE、游标、稳定错误、会话创建/恢复和 Adapter conformance |
| [Tool 扩展规范](docs/tool-extension-specification.md) | 当前实现契约 | Tool schema、注册与激活、Policy、Environment、结果与测试要求 |
| [系统需求与设计基线](docs/agent-system-requirements-baseline.md) | Baseline `1.2` | 功能需求、安全边界、验收标准和明确非目标 |

最重要的兼容性不变量如下：

1. `session_id`、HTTP `transport_session_id`、`event_id`、`agent_id`、`correlation_id` 和 provider tool-call ID 是不同命名空间，不能互换。
2. Event Envelope schema 1 的 `sequence` 从 1 开始严格递增；事件订阅与读取只返回 `sequence > cursor`。
3. `turn_end` 是 turn 完成边界，`session_end`/事件流结束才是 session 生命周期边界；成功 turn 后同一 session 可以继续 follow-up。
4. 授权响应只能原样回送 `approval_request` 携带的 request/correlation ID；超时、断线、中断、关闭或 ID 不匹配都不得产生批准效果。
5. 同一次模型响应中的工具严格串行，每次调用恰好得到一个结果；非成功工具结果不等同于 session `FAILED`。
6. HTTP 首版最多一个未关闭的 transport session；历史 session 与 transport session 分离。历史读取不会创建会话、执行命令或成为第二条 live SSE。
7. 新字段和未知事件必须可安全忽略；不兼容地删除/重命名字段，或改变状态、游标、授权和结束语义，必须先更新权威规范并提升版本。

## 扩展 Tool

自定义工具不会被动态发现。受控 Python 调用方需要显式实现 `Tool` 协议，将工具注册到 `ToolRegistry`，再加入 active set，并保证 `AgentRuntime.active_tools` 与 Registry 完全一致。需要工作区文件或进程能力的工具必须通过 `ToolExecutionContext.environment`；外部服务工具应使用专用窄端口，不能借此获得任意宿主访问能力。

新增工具还必须显式加入执行策略分类，并覆盖 schema、非法参数零副作用、各类 `ToolResult`、correlation ID、授权、事件配对、截断、标准组装和恢复测试。完整流程与模板见 [Tool 扩展规范](docs/tool-extension-specification.md)。

## 安全边界与适用范围

- 这是面向单个本地用户、单个线性 Agent session 的工具，不是远程多用户控制平面。
- HTTP 服务固定为 loopback，没有面向公网部署所需的认证、TLS、CSRF、租户隔离或速率限制。
- Web 端不会接收 API Key、模型配置、workspace 路径或 session 文件路径；localStorage 只保存不透明 transport ID 和事件游标。
- 写文件与 `bash` 是否执行由当前 session 的权限模式决定；`auto`/`--yolo` 表示调用者明确接受自动副作用。
- `bash` 不是沙箱。即使其起始目录是 workspace，命令仍可能访问工作区外资源。
- 当前不实现 Docker Environment、子 Agent、Skill、MCP、并行工具、分支会话、token streaming、远程部署或多用户并发。

## 开发与验证

Python 质量门：

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest
python -m build
```

Web 质量门：

```bash
npm --prefix web run lint
npm --prefix web run type-check
npm --prefix web run test
npm --prefix web run build
```

测试分为 unit、integration、transport conformance、security、architecture 和 acceptance 多个层级。mock/scripted model 用例用于证明本地控制流和契约，不代表真实模型网关、公网安全或宿主机隔离验证。

## License

[MIT](LICENSE)
