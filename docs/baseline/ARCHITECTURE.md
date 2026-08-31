# CodingAgentNeo 架构基线

> 状态：已于 2026-08-31 纳入用户明确的基线范围澄清（介绍视频录制不在本次实现内），可按任务 DAG 串行实施
> 架构基线版本：0.4
> 日期：2026-08-31
> 需求入口：[requirement.md](requirement.md)

本文把需求基线转化为可实施的模块边界、公开契约和验证策略。实际已实现能力和命令证据以 `TASKS.md` 的完成摘要与 `PROGRESS.md` 为准。公开接口、数据模型、状态机、安全边界或部署契约变更时，必须先更新本文和受影响的任务卡。

## 1. 目标、用户与边界

### 1.1 目标用户和成功行为

CodingAgentNeo 面向需要在本地工作区完成编程任务的单个终端用户，以及需要审阅其设计和运行轨迹的评委或开发者。首个可提交版本成功时，用户能够：

1. 在交互式或非交互式 CLI 中提交一个真实编程任务；
2. 让 root Agent 通过模型原生 tool calling 探索、修改并验证工作区；
3. 看到模型、工具、授权、重试、压缩和终止状态；
4. 在中断后保留可解析轨迹，并从线性 session 继续 follow-up；
5. 用测试证明 Runtime 隔离、Environment 替换、路径边界和事件关联契约成立。

### 1.2 强制约束

- Python 3.12；单 Agent、单前台任务、单进程、工具串行执行；同一时刻只有一个 turn 在执行。
- 只接入一种 OpenAI-compatible Chat Completions 原生 tool calling 协议。
- 不使用 Agent 框架或 Agent SDK；Agent Loop、工具、上下文、持久化、权限和错误处理由本项目实现。
- 内置及其他工作区工具的文件、搜索和通用命令副作用全部经 `ExecutionEnvironment`；每个 Agent 的可变状态全部归属显式 `AgentRuntime`。
- system prompt 由组装层显式传入；`ToolRegistry`/`ToolExecutor`/Agent Loop 只依赖通用 Tool 协议，不区分工具来源。
- 前端只能通过 `AgentBackend` 的命令入口和事件流与 Agent 后端交互；后端不得回调任何前端代码，也不得读写终端。
- API Key 只从环境变量或未入库本地配置取得，不进入代码、轨迹、错误或交付物。



### 1.3 首版明确排除

Session tree/branch/fork、MCP 客户端及传输、Skill 发现/解析/加载、RAG/向量库、动态插件、子 Agent、并行工具、后台命令、运行中异步 steering、Plan/Todo 模式、Docker 环境实现、多厂商适配、完整 TUI、Web GUI 或任何 HTTP/进程外前端、服务端代码执行，以及以命令黑名单冒充沙箱，均不在首版范围。架构只保留 Docker、子 Agent、外部上下文、外部 Tool 与**替换前端实现**的低成本扩展边界，不实现其具体行为；首版唯一的前端实现是 CLI。

本文档约束的是基线实现，不是最终提交包。录制介绍/演示视频、产出 mp4、检查视频时长/大小、制作含视频的提交 zip，均不在本基线实现范围。题目原始材料仍要求最终版本提交 2 分钟以内的演示视频；该交付推迟到后续迭代，不得把未产出的视频当作本基线验收证据。本基线可以先交付 `README.txt` 与演示脚本初稿，文案允许后续迭代。

## 2. 质量属性与技术选择


| 领域      | 基线选择                                                     | 责任与理由                             |
| ------- | -------------------------------------------------------- | --------------------------------- |
| 语言与打包   | Python 3.12、`pyproject.toml`、`src/coding_agent_neo/` 布局  | 保持安装、测试和模块边界明确；由 T01 落地并验证        |
| 模型协议    | OpenAI-compatible Chat Completions + 原生 tools/tool calls | 兼容常见网关；禁止用 Markdown/正则作为主要工具协议    |
| API 客户端 | 官方 `openai` Python 客户端，仅作为传输客户端                          | 需求允许厂商客户端；重试、归一化和 Agent 语义仍由本项目负责 |
| 执行模型    | 同步 Agent Loop，串行 tool calls，协作式取消和有界超时                   | 符合首版单前台任务范围，同时在接口保留未来异步可能性        |
| 前端接入    | `AgentBackend` 命令入口 + 按 sequence 游标拉取的事件流                | 前端不持有 Agent 对象也不被后端回调；Loop 在后端独占 worker 线程内仍是同步串行 |
| 持久化     | 每个 session 一个 append-only UTF-8 JSONL 文件                 | 逐事件刷新，便于审计、异常恢复和手工检查              |
| 配置      | CLI > 环境变量 > 未入库 TOML 配置 > 内置默认值                         | 覆盖顺序唯一且可解释；凭据不允许写入已跟踪配置           |
| 测试      | pytest；fake model 与 fake environment 为核心测试替身             | 单元/集成测试不依赖真实 API 或宿主机副作用          |
| 质量门     | Ruff lint/format check、pytest、Python build               | 已由 T01 建立并在 Python 3.12 环境验证              |


关键质量不变量：核心循环可解释；关键事件尽快落盘；错误有界；安全声明真实；共享服务与每 Agent 可变状态分离；前端与 Agent 后端只经命令和事件通信；任何 mock 结果不得被表述为真实外部集成通过。

## 3. 系统上下文与数据流

```mermaid
flowchart LR
    User["终端用户"] --> Frontend["前端：CLI + Terminal Renderer"]
    Frontend -- "AgentCommand" --> Backend["Agent Backend Port"]
    Stream["Event Stream 游标缓冲"] -- "EventEnvelope since=sequence" --> Frontend
    Assembly["Assembly 组装层"] --> Backend
    Prompt["Explicit System Prompt"] --> Assembly
    Backend --> Loop["Agent Loop（后端 worker 线程）"]
    Backend --> Approval["Channel Approval Port"]
    Loop --> Runtime["AgentRuntime"]
    Loop --> Model["Model Client"]
    Loop --> Context["Context Builder / Compactor"]
    Prompt --> Context
    Loop --> Executor["Tool Executor"]
    Executor --> Policy["Execution Policy"]
    Executor --> Approval
    Executor --> Registry["Tool Registry"]
    Executor --> Env["ExecutionEnvironment"]
    Env --> Workspace["逻辑 Workspace"]
    Loop --> Events["Event Emitter"]
    Executor --> Events
    Context --> Events
    Approval --> Events
    Events --> Store["JSONL Session Store"]
    Events --> Stream
```

图中只有 `Frontend` 一侧允许存在终端 I/O。`Assembly` 负责把配置转成一组显式依赖并交给 `Backend`；`Backend` 之下的所有模块都不知道前端类型。



### 3.1 启动与单个 turn

1. 前端解析参数并加载配置，然后调用 Assembly 组装层；组装层构建显式 system prompt，创建共享服务、root `AgentRuntime`、`LocalExecutionEnvironment`、Session Store、Event Emitter 和 Event Stream，返回一个 `AgentBackend`。前端不持有其中任何对象。
2. 前端发送 `SubmitTask`；后端 worker 线程调用 Agent Loop。Environment `start()` 成功后发布 `session_start`、`agent_start`；用户输入形成 `user_message` 并立即持久化。
3. Context Builder 使用显式传入的 system prompt，且只投影当前 `agent_id` 的有效历史；预算超阈值时先执行当前 Runtime 专属 compaction。
4. Model Client 返回归一化 assistant 文本、tool calls、usage 与 finish reason；`assistant_message` 先落盘。
5. 无 tool call 时发布 `turn_end(COMPLETED_TURN)`；前端看到该事件后决定是否发送下一条 `SubmitTask`，非交互前端据 `last_state` 退出。
6. 有 tool calls 时按声明顺序逐个进入工具生命周期，然后回到步骤 3。
7. 每个事件在 Session Store 分配 sequence 并落盘后进入 Event Stream；前端按游标拉取并自行渲染。前端消费快慢不影响 Loop 与持久化。

### 3.2 单个工具调用

1. Agent Loop 为调用生成内部唯一 `correlation_id`，同时保留厂商 `provider_tool_call_id`。
2. Tool Executor 按通用 Tool 协议校验工具是否注册且激活、arguments JSON 与 schema 是否有效，并发布 `tool_call`；不根据工具来源分支。
3. Execution Policy 返回 `allow | ask | deny`；任何策略异常等价于 `deny`，决定以同一 correlation ID 发布。
4. `ask` 由 Channel Approval Port 处理：它发布一个 `approval_request` 事件后阻塞等待前端的 `ApprovalResponse`，超时、取消和关闭一律按拒绝处理；最终结果仍写入同一 correlation ID 的 `policy_decision`。
5. 获准调用通过 `ToolExecutionContext` 使用 Runtime 的 Environment 和 cancellation；Tool 不得直接访问宿主机。
6. 成功、普通失败、拒绝、参数错误和超时均产生一个统一 `ToolResult` 及 `tool_result` 事件。
7. 多个 tool calls 继续串行处理；单个普通失败默认交给模型决定下一步。

### 3.3 Compaction 与恢复

- Session History 是不可改写的事实源；Model Context 是按 agent ID 构建的临时投影。
- Compactor 完整保留组装层显式提供的 system prompt、必要工作区信息和最近完整交互，不拆散 assistant tool call 与 tool result；较早内容和旧 summary 生成增量 summary，持久化覆盖到的 sequence。
- Compaction 调用不暴露工具；失败时保留最近完整交互并注入退化提示，仍超窗则明确失败或触发限制。
- 恢复只重建状态和上下文，不重放已经产生副作用的历史工具；尾部不完整 JSONL 记录被报告并忽略。

### 3.4 关闭、失败与并发规则

- 正常结束依次发布 `agent_end`、`session_end` 并调用 Environment `close()`；中断或未捕获异常也尽最大努力执行同样的落盘与关闭。
- 模型瞬时错误有限指数退避；认证、权限、模型不存在和非法配置立即失败；上下文超限只允许一次强制压缩后重试。
- shell 非零、文件不存在、搜索无结果、编辑不唯一、拒绝和超时是普通 ToolResult，不直接令 Loop 崩溃。
- 首版一次只运行一个 Loop，单次响应中的工具严格串行。Session Store 统一分配无重复单调 sequence；不假设多个 Agent 可共享可写工作区。
- 后端只有一个 worker 线程执行 Loop。允许跨线程访问的对象只有三类且都必须显式加锁或使用线程安全原语：Session Store 的 sequence 分配、Event Stream 的追加与唤醒、approval 通道与 `CancellationSignal`。`AgentRuntime` 的其余可变状态（ContextState、BudgetTracker、active tools）仍只被 worker 线程读写。

### 3.5 前端交互与 approval 反转

- 前端与后端之间只有两条通道：`AgentBackend.send(command)` 与 `AgentBackend.events(since=...)`。后端不持有任何前端提供的可调用对象。
- `SubmitTask` 只在没有 turn 在执行时被接受；turn 执行期间提交新任务是错误而不是排队，首版不提供运行中 steering 或后台输入队列。
- `ApprovalResponse` 与 `Interrupt` 在 turn 执行期间也必须被立即接受：前者交给挂起的 approval 通道，后者设置 Runtime cancellation。两者都不进入 worker 队列。
- `ApprovalResponse.request_id` 必须与当前挂起请求一致；不匹配时当前挂起请求按 fail-closed 拒绝，避免前端串号误批。
- 事件流按 Store 分配的 sequence 排序且不重复。前端只需记住最后一个 sequence 即可断线续订；进程内回放来自 Event Stream 缓冲，跨进程回放来自 JSONL Session Store。
- 拉取模型天然隔离慢消费者：前端渲染慢只会让自身落后，不阻塞 Loop、Executor 或持久化。

## 4. 模块与依赖边界

下列为计划目录契约；目录由对应任务创建，而非本文创建。


| 计划模块                     | 拥有的职责                                        | 禁止拥有或依赖                                    |
| ------------------------ | -------------------------------------------- | ------------------------------------------ |
| `cli.py` / `__main__.py` | 参数、交互输入、事件拉取与渲染驱动、approval 询问、退出码 | 依赖组装、持有 Loop/Runtime/Store/Environment 对象、Agent 决策、路径安全、直接 JSONL 拼写 |
| `assembly.py`            | 由 `AppConfig` 构建显式 system prompt 与全部后端依赖，返回 `AgentBackend` | 终端 I/O、参数解析、运行时可变状态、Agent 决策 |
| `backend.py`             | `AgentCommand`/`AgentBackend` 契约、Event Stream 游标缓冲、worker 线程、Channel Approval Port | 终端 I/O、配置来源、Agent 决策、路径安全、厂商协议 |
| `config.py`              | 配置来源、覆盖、校验、密钥引用                              | 记录真实密钥、启动工具或模型循环                           |
| `runtime.py`             | AgentRuntime、ContextState、BudgetTracker、取消信号 | 进程级“当前 Agent”全局变量、共享可变默认值                  |
| `models.py`              | 内部枚举和不可变/受控数据结构                              | 厂商 response 对象泄漏到其他模块                      |
| `model_client.py`        | 协议请求、响应归一化、分类重试                              | Context 压缩、工具执行、Session 状态                 |
| `environment/base.py`    | 后端无关请求/结果和 Environment Protocol              | Local/Docker 特有字段                          |
| `environment/local.py`   | 唯一宿主机文件、`rg` 与 subprocess 入口                 | 权限 UI、模型协议；Tool 不反向依赖它                     |
| `tools/`                 | 来源无关的 Tool schema、参数校验、注册/激活、内置工具语义       | `open()`、`Path` 实际读写、`subprocess`、宿主机 `rg`、外部协议分支 |
| `policy.py`              | `allow/ask/deny` 决策及 fail-closed             | 实际执行、终端渲染                                  |
| `executor.py`            | correlation 生命周期、授权、异常转 ToolResult、输出投影      | 绕过 Environment、改变 Agent Loop 状态机           |
| `events.py`              | EventEnvelope、发布/订阅接口                        | 具体终端样式、厂商对象、前端游标语义                        |
| `session.py`             | sequence 分配、JSONL append/flush/read 与尾部诊断    | 模型上下文裁剪、历史副作用重放                            |
| `context.py`             | 显式 system prompt 输入、当前 Agent 的上下文投影、预算估算、完整交互分组 | 删除/改写 Session History、访问其他 Agent 内部消息、扫描 Skill/外部资源 |
| `compactor.py`           | 增量 summary 与退化策略                             | 可执行工具、跨 Runtime 状态                         |
| `agent_loop.py`          | 显式依赖驱动的循环、状态和预算终止                            | 全局 workspace/session/tools/预算；直接文件或进程操作    |
| `renderer.py`            | 前端侧组件：把前端拉到的事件转成有界终端展示和统计         | 业务状态权威、持久化事实改写、订阅 EventEmitter、决定循环控制流 |


依赖方向必须满足：`工作区 Tool -> ExecutionEnvironment Protocol`；`Agent Loop -> 抽象服务 + AgentRuntime + 显式 system prompt`；`Event producers -> EventEmitter -> Store/EventStream`；`前端 -> AgentBackend Protocol`。`backend.py`、`assembly.py` 及其下游模块不得 import `cli.py` 或 `renderer.py`，也不得直接读写 `sys.stdin`/`sys.stdout`/`sys.stderr`。只有 `LocalExecutionEnvironment` 可以包含通用宿主机文件和命令进程实现。未来显式配置的外部 Tool adapter 可以拥有其协议所必需的专用传输，但不得获得任意宿主文件或通用 shell 能力。首版不实现该 adapter。

`ToolRegistry` 的注册能力可容纳任意符合 Tool 协议的显式注入对象，但首版组装层只注册六个内置工具。工具注册集可作为受控共享定义；可变 active set 必须与单个 Runtime 绑定，首版组装时必须保证 `AgentRuntime.active_tools` 与传给该 Loop 的 Registry active view 一致，不得出现两个独立可变事实源。

## 5. 数据模型与强制不变量


| 实体                            | 关键字段                                                                                            | 约束与生命周期                                             |
| ----------------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| `AgentRuntime`                | agent/session/parent ID、ContextState、BudgetTracker、active tools、cancellation、policy、environment | 每 Agent 独占可变状态；root 也不得绕过；由 CLI 创建并关闭               |
| `ContextState`                | 最新 summary、covered sequence、最近投影信息                                                              | 不是完整历史；仅属于一个 Runtime                                |
| `BudgetTracker`               | model steps、tool calls、protocol errors、tokens、start/deadline、limits                             | 每次相关操作原子更新；达到上限产生具体 `LIMIT_REACHED` 原因              |
| `NormalizedAssistantResponse` | text、tool calls、usage、finish reason                                                             | 不保存不可序列化厂商对象；tool calls 保持原顺序                       |
| `ToolCall`                    | correlation ID、provider ID、name、raw/parsed arguments                                            | correlation ID 由本系统生成且 session 内唯一；provider ID 独立可选 |
| `ToolResult`                  | IDs、status、model text、metadata、truncated、duration、exit/timeout/path                             | 每个调用恰有一个结果；普通错误也使用同一 schema                         |
| `EventEnvelope`               | schema/session/event/agent/parent ID、sequence、type、correlation、UTC timestamp、payload            | event ID 唯一；sequence 由 Store 单调分配；所有事件含 agent ID    |
| `CompactionRecord`            | summary、covered-through sequence、source bounds、failure metadata                                 | append-only；不删除源事件；不得拆散工具交互                         |
| `EnvironmentRequest/Result`   | 逻辑相对路径或命令、限制、状态、后端无关元数据                                                                         | Environment 最终执行 workspace 校验；结果不得泄漏 Docker 等后端细节   |
| `AgentCommand`                | `SubmitTask(text)`、`ApprovalResponse(request_id, approved)`、`Interrupt(reason)`、`CloseSession(reason)` | 不可变、可 JSON 化、不携带可调用对象或前端句柄；由前端构造并只在后端边界解释 |


其他不变量：

- 时间戳使用带 `Z` 的 UTC ISO 8601；超时和耗时计算使用单调时钟。
- tool call、policy decision、tool result 及相关错误共享 correlation ID；agent ID 只表示归属，不得混作调用关联。
- JSONL 每行一个完整 JSON object；关键事件 append 后 flush。单事件持久化上限与模型可见上限分别配置并明确记录截断。
- Registered Tools 与 Active Tools 分离；未激活等同协议错误，不得执行。
- 两个 Runtime 的 ContextState、BudgetTracker、active tools、cancellation 和 Environment 调用记录不得共享可变对象。
- `AgentCommand` 是前端影响后端的唯一手段；后端影响前端的唯一手段是事件流与只读的 `last_state`。任何一侧都不得持有另一侧的回调。
- 事件在被 Event Stream 暴露给前端之前必须已由 Session Store 分配 sequence 并落盘，因此前端看到的事实与审计轨迹永不分叉。

## 6. 公开契约

### 6.1 CLI 与进程状态

CLI 是 `AgentBackend` 之上的一个前端实现，本节的用户可见契约不因解耦而改变。计划入口为 `coding-agent-neo`（等价支持 `python -m coding_agent_neo`）：

- 无任务参数时进入交互模式；`--task TEXT` 或 stdin 提供一次性非交互任务。
- 公共选项至少包括：`--model`、`--api-base`、`--api-key-env`、`--workspace`、`--session-dir`、`--resume`、`--approval-mode ask|auto|deny`、`--max-steps`、`--max-tool-calls`、`--max-wall-seconds`、`--command-timeout`、`--context-window`、`--reserved-output-tokens`、`--model-output-limit`、`--session-output-limit`。
- `--yolo` 可以作为 `--approval-mode auto` 的明确别名；非交互模式若为 `ask`，需要确认的调用必须拒绝而非等待输入。
- 正常 turn 完成返回 0；配置/认证/未恢复系统错误、`FAILED` 返回非零；`INTERRUPTED` 与 `LIMIT_REACHED` 使用文档化的非零退出码，由 T10 固化并测试。退出码由 `AgentBackend.last_state` 推导，不再依赖直接调用 Loop 得到的返回值。
- 至少完成一次 turn 后，进程退出前在诊断流写出一行 resume 提示：`To continue this session, run: coding-agent-neo --resume <session_id>`。非交互模式写 stderr（stdout 仍仅含最终 assistant 文本）；交互模式写 stdout。未提交任务不提示。`--session-dir` 不是默认相对路径时，提示中带上该选项，保证复制即可继续。

配置覆盖顺序为 CLI、`CODING_AGENT_NEO_*` 环境变量、未入库本地 TOML、内置默认值。API Key 的值只通过 `--api-key-env` 指定的环境变量读取；禁止提供会把 key 写入 argv 或已跟踪文件的 `--api-key` 选项。精确默认值由 T01/T10 在不改变上述契约的前提下固定。

### 6.2 Model Client

`ModelClient.complete(messages, tools, parameters) -> NormalizedAssistantResponse` 是 Agent Loop 唯一模型入口。调用请求使用 OpenAI-compatible roles 和原生 tool schema；compaction 调用传空 tools。错误分类为 `retryable`、`fatal`、`context_overflow`，且日志只含脱敏诊断。

### 6.3 ExecutionEnvironment

Environment Protocol 暴露 `start()`、`close()`、`read_file()`、`list_files()`、`search()`、`write_file()`、`edit_file()`、`run_command()`，每项接收后端无关 request 和 cancellation，返回结构化 result。`LocalExecutionEnvironment` 的 workspace 在 start 时解析；现有路径解析真实路径，待创建路径解析最近存在父目录，拒绝绝对路径、`..` 和 symlink 逃逸。

### 6.4 Tool 与策略

Tool 至少公开 `name`、`description`、JSON-compatible schema、参数校验和 `execute(arguments, ToolExecutionContext)`。Registry、Executor 和 Loop 只依赖该协议，不区分内置、fake 或未来 adapter Tool。首版实际注册并激活的工具仍只有 `read_file`、`list_files`、`search`、`write_file`、`edit_file`、`bash`。默认策略：工作区内结构化文件工具 allow；交互 bash ask；越界或不安全参数 deny；auto/yolo 下 bash allow；未知非内置工具和策略异常 deny。

### 6.5 事件、状态与错误

事件 `schema_version` 首版为 `1`。标准事件至少包括 `session_start`、`agent_start`、`user_message`、`assistant_message`、`tool_call`、`policy_decision`、`approval_request`、`tool_result`、`compaction`、`retry`、`turn_end`、`error`、`agent_end`、`session_end`。payload 按类型版本化，不包含凭据。

`approval_request` 与其 `tool_call`、`policy_decision`、`tool_result` 共享 correlation ID，payload 至少包含 `request_id`（等于该 correlation ID）、`tool_name`、经脱敏与截断的参数摘要，以及等待上限秒数。它是一个普通的持久化事件，因此 approval 询问过程本身也进入审计轨迹。

Turn/运行状态为 `RUNNING`、`WAITING_FOR_APPROVAL`、`COMPLETED_TURN`、`LIMIT_REACHED`、`INTERRUPTED`、`FAILED`。协议错误对模型可见并计数；Environment/Tool 普通失败转 ToolResult；只有不可恢复系统错误结束 Loop，同时尽力记录 `error` 和结束事件。

### 6.6 前端与 Agent 后端契约

baseline 建立的完整命令、事件 payload、状态机、游标、授权和兼容性语义，现由重命名后的 [Agent 后端接口规范](../agent-backend-interface.md) 保存。本节保留 baseline 架构摘要；后续 adapter 的前端接入方式另见 [Agent 适配层接口规范](../agent-transport-interface.md)。

`AgentBackend` 是前端可见的唯一后端接口，任何前端实现（首版 CLI，未来 Web GUI）都只依赖它：

```python
class AgentBackend(Protocol):
    @property
    def last_state(self) -> RuntimeState: ...
    def send(self, command: AgentCommand) -> None: ...
    def events(self, *, since: int = 0) -> Iterator[EventEnvelope]: ...
    def close(self) -> None: ...
```

- `send` 是线程安全且非阻塞的。`SubmitTask` 交给 worker 队列；`ApprovalResponse` 交给挂起的 approval 通道；`Interrupt` 设置 Runtime cancellation；`CloseSession` 请求有序停机。turn 执行期间提交 `SubmitTask` 抛出明确异常而不是排队，会话关闭后提交任何命令同样是错误。
- approval 等待上限与 worker 停机等待上限都是组装层可注入的参数，默认值必须文档化，测试可注入小值以获得确定的超时行为。
- `events(since=n)` 先返回缓冲中 sequence 大于 `n` 的全部事件，再阻塞等待新事件；后端关闭且缓冲排空后迭代器正常结束。同一后端允许被多次调用与重新进入，语义只由游标决定。内部等待必须使用有界超时轮询，以保证前端主线程的 `KeyboardInterrupt` 及时生效。
- `last_state` 是只读的最近一次已知运行状态，供前端决定退出码和是否继续 follow-up；它是事件流的派生量，不构成第二个事实源。
- `close()` 幂等：请求 worker 停止、按上限等待其退出、关闭 Loop 与 Session Store，并让所有活跃迭代器结束。
- 组装层入口为 `build_local_backend(config, *, interactive) -> AgentBackend`，是前端获得后端实例的唯一方式。

首版只实现 `LocalAgentBackend`（同进程、一个 worker 线程）。跨进程或网络传输不在首版范围；未来实现必须保持同样的命令集合、事件语义和游标语义，而不是扩展本接口。

## 7. 安全、隐私与信任边界

1. 结构化文件工具的安全边界是逻辑 workspace，由 Environment 最终强制；Tool Executor 的检查只能作为纵深防御。
2. Local bash 继承启动进程的宿主用户权限和网络能力，不是沙箱；初始 cwd 与超时不能阻止其访问 workspace 外资源。CLI 和 README 必须明确这一点。
3. Environment 不得因后端失败而回退到未授权宿主机访问；未来 Docker 后端必须自行声明文件、网络、进程和资源边界。
4. 密钥、请求头、Authorization、私有配置值必须在事件和异常边界统一脱敏；示例只使用占位符。
5. approval 决策是运行时事件。用户拒绝、无交互输入和策略异常均不得偷偷执行。approval 通道 fail-closed：等待超时、被中断、后端关闭或 `request_id` 不匹配一律按拒绝处理，且拒绝原因必须出现在 `policy_decision` 中。前端不可用绝不能退化为自动批准。
6. 禁止把真实用户数据、session 轨迹、构建产物、虚拟环境和本地配置默认提交到版本库；T01 负责 `.gitignore` 和示例配置。
7. 未来外部 Tool adapter 的专用传输是独立信任边界：必须由用户显式配置，不得从未信任项目自动启动，不得让外部声明绕过本地 active tools 或 Execution Policy，也不得将凭据投影给模型。首版不实现该边界的具体 adapter。

## 8. 部署与运维契约

- 首版是 macOS/常见 Linux 上的本地 Python 进程，无服务器、数据库或守护进程；每次前台运行最多一个活跃 Agent Loop。
- 首版不读取 Skill/MCP 配置，不扫描 Skill 目录，不发起 MCP 网络连接或启动 MCP 进程，也不新增任何相关 CLI 选项。
- workspace 与 session 目录通过配置显式给出。session 文件命名必须避免冲突并可由 `--resume` 精确选择。
- `rg` 是 Local search 的可选外部依赖：启动或首次使用时检测；不可用必须明确报错或使用文档化退化实现，不能静默返回空结果。
- Ctrl+C 由前端主线程捕获后转成 `Interrupt` 命令；后端设置 Runtime cancellation，终止当前可取消操作，尽力 flush/close，再以 `INTERRUPTED` 结束。`LocalExecutionEnvironment` 在等待子进程时轮询 cancellation，因此运行中的命令仍会被真正打断。进程崩溃后的恢复依赖已 flush 的完整 JSONL 行。
- 终端、模型上下文和持久化可以有不同输出上限，但都投影自同一 ToolResult；显示必须保留退出码、超时和截断事实。
- 运行统计仅在 API 返回 usage 时累计 token；价格只有在可配置且来源明确后才计算，不硬编码易过期价格。

## 9. 验证策略


| 层级   | 真实依赖                                         | 主要证明内容                                   | 限制                        |
| ---- | -------------------------------------------- | ---------------------------------------- | ------------------------- |
| 单元测试 | fake clock/model/environment、注入的 fake Tool、临时目录 | 数据不变量、通用 Tool 协议、错误分类、路径边界、输出截断、Runtime 隔离 | 不能证明真实网关兼容或宿主 shell 隔离 |
| 组件测试 | `LocalExecutionEnvironment` + 临时 workspace   | 六类环境操作、symlink 逃逸、rg 检测、timeout/cancel   | 只代表当前 OS 权限模型             |
| 集成测试 | scripted fake model + fake/local environment | 完整 Loop、非内置 fake Tool、显式 system prompt、工具失败修正、事件顺序、预算、compaction、resume | fake 不证明任何真实 Skill/MCP 集成或模型行为 |
| 前后端契约测试 | 内存 `AgentBackend` + scripted fake model | 命令语义、approval 反转四条路径、事件游标连续性与重新 attach、interrupt 时效、前端无 Agent 对象依赖 | 不证明跨进程或网络前端；线程时序测试需注入超时以避免 flaky |
| 验收测试 | 小型缺陷仓库 + 用户提供的测试 API 配置                      | AC-01 真实编程闭环和终端体验                        | 必须明确记录模型、环境和未执行项；不得提交 key |
| 静态审查 | `rg`/代码审查 | 工作区 Tool 无宿主机 I/O、Loop 无工具来源分支、Context 无外部资源扫描、无全局 Runtime/凭据、后端不 import 前端模块也不碰标准流、依赖方向正确 | 不能替代动态安全测试 |


## 10. 需求追踪


| 需求范围                     | 架构落点        | 主要任务                | 主要验收                    |
| ------------------------ | ----------- | ------------------- | ----------------------- |
| FR-01～FR-04 核心循环与终止      | 3.1、3.2、6.5 | T08                 | AC-01～AC-03、AC-09       |
| FR-05～FR-07 CLI 与配置      | 6.1、6.6、8   | T10、T14            | AC-01、AC-05、AC-10       |
| FR-08～FR-09 模型协议与重试      | 2、3.4、6.2   | T07                 | AC-02、AC-08             |
| FR-10～FR-12 工具与结果        | 3.2、4、5、6.4 | T04、T05             | AC-02、AC-03、AC-11、AC-13 |
| FR-13～FR-15 路径、策略与 shell | 6.3、6.4、7   | T03、T05             | AC-04、AC-05、AC-11       |
| FR-16～FR-19 历史、事件与恢复     | 3.3、5、6.5、8 | T06、T11             | AC-06、AC-07、AC-13       |
| FR-20～FR-24 上下文与输出       | 3.3、5、8     | T04、T09             | AC-08                   |
| FR-25～FR-28 错误与限制        | 3.4、5、6.5   | T05、T07、T08、T09     | AC-02、AC-03、AC-09       |
| FR-29～FR-30 展示与统计        | 3、8         | T10、T14            | AC-01、AC-06             |
| FR-31～FR-34 Runtime/环境/事件边界 | 4、5         | T02、T03、T05、T06、T08 | AC-11～AC-14             |
| FR-35～FR-36 上下文/Tool 扩展边界 | 3、4、6.4、9 | T08、T09、T12 | AC-01、AC-08（仅 fake 边界证据） |
| NFR-06 Loop 不依赖具体终端 UI | 3、3.5、4、6.6 | T14                 | AC-06、AC-14             |
| NFR-01～NFR-07            | 2、4、7～9     | T01～T12、T14        | AC-04、AC-06、AC-10～AC-14 |
| 提交物与面试可解释性               | 1、9         | T12、T13             | 真实演示证据、README.txt 初稿、演示脚本；录制视频不在本基线范围 |


## 11. 变更控制

需求正文列出的基线变更条件全部适用。特别是改变模型协议、工具集合、默认权限、Session 语义、工作区 Tool/Environment 单向依赖、显式 system prompt 输入、外部 Tool 适配边界或安全声明时，必须先修改需求基线；其他公开契约变化至少先修改本文、相关任务和决策日志。当前文档已通过用户变更审阅，并已纳入 2026-08-31 的基线范围澄清；实现仍必须按 `TASKS.md` 的依赖图一次派发一个任务。

2026-08-30 的前后端解耦属于架构级公开契约变更，不触及需求正文第 22 节的任何变更条件：模型协议、六个工具、线性 Session、默认权限语义、历史与上下文分离、自动 compaction、Tool 对 Environment 的单向依赖、`AgentRuntime` 的非全局性、Environment 契约与安全声明均未改变，也未引入 Agent 框架或远程执行控制平面。Web GUI 仍是明确排除项，本次只建立可替换前端的接口边界。新增的 `AgentBackend`、`AgentCommand` 与 `approval_request` 事件构成新的公开契约，其后续变更适用本节规则。

2026-08-31 的基线交付范围澄清也不触及需求正文第 22 节：产品功能、安全模型和公开运行契约不变。变化只是把最终提交物中的介绍视频录制从本基线实现与 T13 验收中拿掉；需求正文与题目原始材料仍保留该最终交付，后续迭代再补。README 与演示脚本仍在本基线范围内，允许作为初稿后续修改。
