# CodingAgentNeo 系统需求与关键设计基线

> 状态：已确认基线  
> 基线版本：1.1  
> 基线日期：2026-08-27  
> 适用范围：CodingAgentNeo 首个可提交版本

版本 1.1 在原有功能基线上正式加入 AgentRuntime、ExecutionEnvironment、agent ID 和 correlation ID 四项扩展边界；Docker 与子 Agent 的具体实现仍不属于首版范围。

## 1. 文档目的

本文档是 CodingAgentNeo 的系统需求与关键设计基线，用于约束后续架构设计、编码、测试、演示和答辩。除非明确发起需求变更，后续实现应以本文档为准。

本文档基于以下材料制定：

- [项目总体要求](./requirement.md)
- [mini-SWE-agent 设计决策分析](./temp/reference-design/mini-swe-agent-decisions.md)
- [Pi Agent 设计决策集合](./temp/reference-design/pi-agent-decisions.md)

本文中的关键词含义如下：

- **必须**：首个可提交版本不可缺少的要求。
- **应该**：原则上应实现；若因时间或技术风险调整，必须记录原因。
- **可以**：不影响基线验收的增强项。

## 2. 项目定位

CodingAgentNeo 是一个小而完整、可恢复、可观察、执行受控的单智能体编程 Agent。它通过模型原生的 tool calling 接口，自主读取和修改工作区文件、搜索代码、执行命令，并循环处理工具结果，直至完成当前编程任务。

系统采用 mini-SWE-agent 的简单线性执行循环，同时吸收 Pi Agent 的以下设计：

- 多工具注册与运行时激活分离；
- 持久化会话历史与模型工作上下文分离；
- append-only 事件轨迹；
- 自动上下文压缩；
- 工具执行策略与运行时事件机制；
- AgentRuntime 与 ExecutionEnvironment 的显式运行边界。

系统不以构建通用 Agent 平台为目标，不引入与核心编程闭环无关的复杂能力。

## 3. 项目约束

### 3.1 外部规则

1. 系统不得基于现成 Agent 产品封装界面。
2. 系统不得使用 LangChain、LlamaIndex、OpenAI Agents SDK、Claude Agent SDK、AutoGen、CrewAI 等 Agent 框架或 SDK。
3. 系统可以使用模型厂商 API 客户端库、OpenAI 兼容网关和模型原生 tool calling。
4. 文件操作、命令执行、历史管理、上下文构建、工具解析、循环终止和错误处理等核心逻辑必须由本项目实现。
5. 系统不得依赖 API 服务端托管的代码执行或文件工具。
6. API Key 等凭据必须通过环境变量或未入库配置提供，禁止写入仓库、README.txt 或演示视频。

### 3.2 技术与范围约束

1. 首个版本使用 **Python 3.12** 实现。
2. 首个版本只要求支持一种 **OpenAI-compatible、支持原生 tool calling 的模型协议**。
3. 模型访问层必须保留清晰接口，但不要求首个版本实现多个模型厂商适配器。
4. 首个版本以单 Agent、单前台任务、工具串行执行为边界。
5. 系统不宣称提供操作系统级安全沙箱。
6. 工具不得直接读写宿主机文件或启动宿主机进程，所有执行能力必须经由 ExecutionEnvironment 接口获得。
7. Agent Loop 和每个 Agent 的运行状态不得依赖进程级可变全局变量。

## 4. 总体目标

系统必须达到以下目标：

1. 能完成真实的编程任务闭环：理解任务、探索代码、修改文件、执行验证并给出最终结果。
2. Agent 的每次模型响应、工具调用、工具结果、权限决定、压缩和异常均可观察、可追溯。
3. 即使运行中断或上下文发生压缩，完整会话事实仍尽可能保留。
4. 核心代码规模和抽象层级保持可理解，能够在面试中解释每项关键决策。
5. 对文件访问和高权限命令建立清晰、诚实的安全边界。
6. 在不实现 Docker 和子 Agent 的前提下，为后续环境替换与多 Runtime 调度保留低成本扩展边界。

## 5. 总体架构

系统划分为以下核心模块：

```text
CLI / Terminal Renderer
    ├── Shared Services
    │    ├── Model Client
    │    ├── Tool Registry
    │    └── Event Emitter ─── Session Store (JSONL)
    │
    └── Agent Loop(runtime)
         ├── AgentRuntime
         │    ├── Agent Identity / Context State
         │    ├── Budget Tracker / Active Tools
         │    ├── Cancellation Signal / Execution Policy
         │    └── ExecutionEnvironment
         ├── Context Builder ── Compactor
         └── Tool Executor
                 └── ToolExecutionContext
                         └── ExecutionEnvironment
```

模块职责如下：

| 模块 | 职责 |
| --- | --- |
| CLI | 解析配置，启动交互式或非交互式运行，接收用户输入和确认 |
| Agent Loop | 使用显式传入的 AgentRuntime 驱动模型、工具和结果之间的循环；自身不依赖全局运行状态 |
| AgentRuntime | 聚合一个 Agent 独有的身份、上下文状态、预算、active tools、取消信号、执行策略和环境引用 |
| Model Client | 调用 OpenAI-compatible API，将响应归一化为内部模型 |
| Context Builder | 从持久化历史投影出本次模型请求所需的工作上下文 |
| Compactor | 在接近上下文预算时增量总结较早历史 |
| Tool Registry | 注册工具、选择 active tools、生成工具 schema、分发调用 |
| Tool Executor | 接收带稳定 correlation ID 的调用，校验参数、应用策略并通过 ToolExecutionContext 执行工具 |
| ToolExecutionContext | 向工具暴露当前 agent ID、correlation ID、ExecutionEnvironment、取消信号和受控事件接口 |
| Execution Policy | 在执行前给出 allow、ask 或 deny 决策 |
| ExecutionEnvironment | 抽象文件、搜索和进程执行；首版实现 LocalExecutionEnvironment，未来可替换为 Docker 实现 |
| Event Emitter | 发布标准化运行时事件，使核心循环与 UI、存储解耦 |
| Session Store | 以 append-only JSONL 形式持久化完整会话轨迹 |
| Terminal Renderer | 实时展示模型输出、工具调用、工具结果、费用与状态 |

首个版本可以让部分模块共享进程和数据结构，但其职责边界必须在代码中保持清晰。

### 5.1 核心对象关系

下列伪代码表达强制的依赖方向，不规定最终类名或完整方法签名：

```python
class AgentRuntime:
    agent_id: str
    session_id: str
    parent_agent_id: str | None
    context_state: ContextState
    budget: BudgetTracker
    active_tools: set[str]
    cancellation: CancellationSignal
    execution_policy: ExecutionPolicy
    environment: ExecutionEnvironment


class ExecutionEnvironment(Protocol):
    def start(self) -> None: ...
    def close(self) -> None: ...
    def read_file(self, request, cancellation) -> FileResult: ...
    def list_files(self, request, cancellation) -> ListResult: ...
    def search(self, request, cancellation) -> SearchResult: ...
    def write_file(self, request, cancellation) -> FileResult: ...
    def edit_file(self, request, cancellation) -> FileResult: ...
    def run_command(self, request, cancellation) -> CommandResult: ...


class ToolExecutionContext:
    agent_id: str
    correlation_id: str
    environment: ExecutionEnvironment
    cancellation: CancellationSignal
```

具体实现可以采用同步或异步方法，但取消和超时语义必须由接口保留。若未来引入并行子 Agent，可以在不改变工具业务语义的前提下将实现迁移为异步调度。

ContextState 只保存当前 Agent 的模型工作上下文状态、最近 compaction 指针及相关投影信息，不替代 Session Store 中的完整事件历史。BudgetTracker 保存当前 Agent 的模型轮数、工具调用、token、墙钟时间等计数和剩余额度。

依赖方向必须满足：

1. Tool 依赖 ExecutionEnvironment 抽象，不依赖 LocalEnvironment 或 DockerEnvironment 具体类。
2. Tool 不得直接调用 `open()`、`pathlib.Path` 的宿主机读写方法、`subprocess` 或宿主机 `rg` 完成实际操作。
3. LocalExecutionEnvironment 是首版宿主机执行能力的唯一入口。
4. AgentRuntime 保存每个 Agent 独有的可变状态；Model Client、Tool Registry、Session Store 等无状态或受控共享服务不应被复制进 Runtime。
5. Agent Loop 通过构造参数或方法参数取得依赖，可以在同一进程中独立实例化多个互不污染的 Loop 和 Runtime。

## 6. 核心执行模型

### FR-01：Agent Loop

系统必须采用持续的 `LLM → Tool Calls → Tool Results → LLM` 循环：

```text
接收用户消息
    ↓
持久化 user event
    ↓
从 Session History 构建 Model Context
    ↓
必要时执行 Compaction
    ↓
请求模型并持久化 assistant event
    ↓
响应是否包含 tool calls？
    ├── 是：依次校验、授权、执行和记录，然后继续请求模型
    └── 否：结束当前 turn，返回 assistant 文本
```

### FR-02：内部运行状态

系统必须能够区分并记录以下状态或等价的退出原因：

- `RUNNING`：Agent 正在执行当前 turn；
- `WAITING_FOR_APPROVAL`：工具等待用户授权；
- `COMPLETED_TURN`：模型返回无工具调用的最终文本；
- `LIMIT_REACHED`：达到步骤、时间或调用数量限制；
- `INTERRUPTED`：用户中断；
- `FAILED`：发生无法恢复的系统错误。

这些状态用于控制流程、输出结果和记录轨迹，不要求引入通用工作流引擎。

### FR-03：多工具调用

1. 模型单次响应可以包含零个、一个或多个 tool call。
2. 多个 tool call 必须按照模型声明顺序串行执行。
3. Agent Loop 必须为每个 tool call 生成内部稳定的 correlation ID；调用、授权和结果事件均使用该 ID 关联。
4. 模型厂商返回的 tool-call ID 应作为独立字段保留，不得代替内部 correlation ID。
5. 每个 tool call 都必须产生与其 correlation ID 对应的 tool result，包括成功、失败、拒绝或参数错误。
6. 单个工具失败原则上不应直接终止 Agent Loop；模型应能看到失败结果并决定下一步。

### FR-04：Turn 终止

1. 当 assistant 返回非空文本且不包含 tool call 时，当前 turn 正常结束。
2. 系统不使用藏在 shell 命令中的特殊提交字符串作为主要终止协议。
3. 交互式会话在 turn 结束后仍可接受下一条用户消息。
4. 非交互式模式在当前 turn 结束后退出进程，并使用明确的进程退出状态表示结果。

## 7. 产品形态与 CLI

### FR-05：交互式运行

系统必须提供交互式 CLI，至少支持：

- 输入初始任务；
- 查看模型回复、工具调用与工具结果；
- 回答工具执行确认；
- 在一个 turn 完成后继续发送 follow-up；
- 使用 Ctrl+C 中断当前运行，并尽量保存已有轨迹。

运行中异步 steering、后台输入队列和完整 TUI 不属于首个版本基线。

### FR-06：非交互式运行

系统必须提供单次非交互式运行方式，能够从命令行参数或标准输入接收任务，并将最终结果输出到标准输出。

非交互式运行若可能触发需要人工确认的工具，调用方必须显式选择自动执行策略；否则该工具调用应被拒绝，而不是永久等待输入。

### FR-07：运行配置

系统必须支持配置以下项目：

- 模型名称；
- API Base URL；
- API Key 对应的环境变量名或标准环境变量；
- workspace 路径；
- session 输出路径；
- approval mode；
- 最大模型轮数或步骤数；
- 最大工具调用数；
- 最大墙钟时间；
- 单条命令超时；
- 模型上下文窗口和预留输出 token；
- 工具输出的模型可见上限和持久化上限。

配置可以来自命令行、环境变量和本地配置文件，但必须规定清晰的覆盖顺序。

## 8. 模型访问与协议

### FR-08：模型接口

Model Client 必须：

1. 接收标准化 messages、active tool schemas 和模型参数；
2. 调用 OpenAI-compatible 原生 tool calling 接口；
3. 将不同 API 响应归一化为 assistant 文本、tool calls、usage 和结束原因；
4. 不依赖 Markdown 代码块或正则表达式解析主要工具调用；
5. 不将 API Key、请求头或其他凭据写入事件日志。

是否实现流式输出不属于首个版本的强制要求。若实现流式输出，不得改变归一化后的会话语义。

### FR-09：模型调用重试

系统必须区分可重试与不可重试错误：

| 错误类型 | 默认处理 |
| --- | --- |
| 网络瞬时失败、限流、部分服务端错误 | 有上限的指数退避重试 |
| 认证失败、权限不足、模型不存在、非法配置 | 立即失败 |
| 上下文窗口超限 | 尝试触发一次强制压缩并重试；仍失败则终止 |

所有重试必须有次数上限，并记录重试原因，但不得泄露凭据。

## 9. 工具系统

### FR-10：Tool Registry

系统必须实现小型工具注册中心。每个工具至少包含：

- `name`；
- `description`；
- JSON-compatible 参数 schema；
- 参数校验逻辑；
- 接收 arguments 和 ToolExecutionContext 的 `execute` 实现。

Registered Tools 与 Active Tools 必须分离：工具可以存在于系统中，但只有 active tools 会暴露给模型并允许调用。

首个版本不要求从第三方包或项目目录动态加载工具。

### FR-11：内置工具

首个版本必须提供以下工具或语义等价的工具：

| 工具 | 最低能力 |
| --- | --- |
| `read_file` | 按路径读取文本文件，支持有界的行范围或输出范围 |
| `list_files` | 有界地列出目录内容，避免无限递归和超大输出 |
| `search` | 在 workspace 内按文本或正则搜索，并限制结果数量 |
| `write_file` | 创建文件或完整覆盖文件，写入前执行路径检查 |
| `edit_file` | 通过精确旧文本替换等可验证方式修改已有文件 |
| `bash` | 在 workspace 中执行通用 shell 命令，支持超时和退出码 |

工具只能通过 ToolExecutionContext 中的 ExecutionEnvironment 完成文件、搜索和命令操作。LocalExecutionEnvironment 应优先使用 Python 标准库实现；其 `search` 可以调用宿主机 `rg`，不可用时应给出明确错误或提供合理退化方案。未来 DockerExecutionEnvironment 应在容器内部执行对应操作，不得回退到宿主机文件系统。

### FR-12：ToolResult

所有工具必须返回符合统一 schema 的结构化结果，至少包含：

- 内部 correlation ID；
- 可选的模型厂商 tool-call ID；
- 成功或失败状态；
- 供模型查看的文本；
- 结构化元数据；
- 是否被截断；
- 执行耗时；
- 对命令工具而言的退出码和超时状态；
- 对文件修改工具而言的目标路径。

普通工具错误必须被转换为 ToolResult，而不是未经处理地穿透 Agent Loop。

## 10. 权限与执行边界

### FR-13：文件路径限制

所有结构化文件操作必须被限制在当前 ExecutionEnvironment 的逻辑 workspace 内。边界校验最终由 ExecutionEnvironment 负责，Tool Executor 可以在调用前进行额外校验：

1. 对已存在路径，应解析真实路径后验证其位于 workspace 内。
2. 对待创建路径，应解析最近已存在父目录的真实路径后验证边界。
3. 必须阻止 `..`、绝对路径和符号链接造成的 workspace 逃逸。
4. 越界请求必须返回拒绝结果并记录安全事件，不得执行。

### FR-14：执行策略

工具执行前必须经过统一 Execution Policy，并得到以下一种决策：

- `allow`：直接执行；
- `ask`：请求用户确认后执行或拒绝；
- `deny`：不执行并返回拒绝原因。

首个版本默认策略如下：

| 操作 | 默认策略 |
| --- | --- |
| workspace 内的 `read_file`、`list_files`、`search` | allow |
| workspace 内的 `write_file`、`edit_file` | allow |
| `bash` | 交互模式 ask |
| 文件工具越界、参数无法安全解析 | deny |
| 显式 `--yolo` 或等价自动执行模式 | bash allow |

安全策略自身发生异常时必须 fail-closed，即拒绝本次调用。

### FR-15：Shell 安全声明

1. `bash` 必须以当前 ExecutionEnvironment 的逻辑 workspace 作为初始工作目录，并设置单次执行超时。
2. 系统不得宣称仅凭工作目录或字符串黑名单就能限制 shell 访问 workspace 外的资源。
3. 系统默认不依赖命令黑名单作为安全边界。
4. LocalExecutionEnvironment 中的 `bash` 继承启动 CodingAgentNeo 的操作系统用户权限；这一限制必须在用户文档中明确说明。
5. 其他 ExecutionEnvironment 必须明确声明自己的文件系统、网络、进程和资源隔离能力，不得由 Tool 根据环境类型猜测安全边界。

## 11. Session History 与运行轨迹

### FR-16：append-only JSONL

Session Store 必须采用 append-only JSONL 或语义等价的逐事件持久化方式。每条事件至少包含：

- schema version；
- session ID；
- event ID；
- agent ID；
- 可选的 parent agent ID；
- 单调递增的 sequence number；
- event type；
- 与工具生命周期事件相关时的 correlation ID；
- timestamp；
- event payload。

每条关键事件产生后应尽快写入并刷新，使进程异常退出时尽可能保留轨迹。

### FR-17：标准事件类型

系统至少应记录以下事件或语义等价内容：

- `session_start`；
- `agent_start`；
- `user_message`；
- `assistant_message`；
- `tool_call`；
- `policy_decision`；
- `tool_result`；
- `compaction`；
- `retry`；
- `turn_end`；
- `error`；
- `agent_end`；
- `session_end`。

### FR-18：历史与上下文分离

1. Session History 是完整事实记录和恢复依据。
2. Model Context 是 Context Builder 从历史生成的临时投影。
3. 模型上下文发生裁剪或压缩时，不得删除或改写原始历史事件。
4. 持久化事件结构不得直接依赖某个厂商不可序列化的原始 response 对象。
5. 可以记录归一化 usage、finish reason、tool calls 和必要的诊断信息。
6. Context Builder 必须按当前 AgentRuntime 的 agent ID 构建上下文；其他 Agent 的内部消息不得隐式进入当前 Agent 的上下文。
7. 未来子 Agent 的结果必须通过显式的委托结果消息进入父 Agent 上下文，而不是直接拼接子 Agent 的完整轨迹。

### FR-19：会话恢复

系统应该能够从已有 session 恢复线性会话：

1. 校验 JSONL 中已完成事件；
2. 忽略或报告最后一条不完整记录；
3. 恢复最新有效 compaction 和其后的消息；
4. 恢复 root Agent 的 agent ID、ContextState、预算计数、active tools 和取消状态的合理初始值；
5. 允许用户继续发送 follow-up。

恢复不要求重放已经产生外部副作用的工具调用。

## 12. 上下文管理与自动压缩

### FR-20：上下文预算

Context Builder 必须针对当前 AgentRuntime，在调用模型前估算以下内容的输入占用：

- system prompt；
- 工具 schemas；
- compaction summary；
- 当前 AgentRuntime 的有效消息；
- 工具调用和结果；
- 为模型输出预留的 token。

首个版本可以使用带安全余量的近似 token 估算，但必须允许配置模型上下文窗口，并避免等到 API 报错后才进行常规压缩。

### FR-21：自动增量压缩

当某个 AgentRuntime 的估算上下文超过安全阈值时，系统必须仅压缩该 Runtime 的上下文：

1. 保留 system prompt 和必要的工作区信息；
2. 保留最近若干完整交互，且不得拆散 assistant tool call 与对应 tool result；
3. 将较早历史连同上一次 summary 交给模型生成新的增量 summary；
4. 在 summary 中重点保留：原始任务、约束、重要决策、已读取或修改的文件、执行过的测试及结果、未解决问题和下一步；
5. 将 summary 作为 `compaction` 事件持久化，并记录其覆盖到的 sequence number；
6. 后续上下文使用最新 summary 加压缩点之后的原始消息。

Compaction 请求不得向总结模型暴露可执行工具。

### FR-22：压缩失败兜底

若 compaction 调用失败，系统必须采用有界退化策略，而不是无限重试：

- 保留最近完整交互；
- 注入一条明确说明早期上下文未完全载入的内部提示；
- 记录 compaction 失败事件；
- 如果仍无法满足上下文窗口，则以明确的 `FAILED` 或 `LIMIT_REACHED` 原因结束。

## 13. 输出管理

### FR-23：模型可见输出截断

工具输出超过模型可见上限时，系统必须：

1. 保留输出头部和尾部；
2. 在中间插入明确的截断标记；
3. 告知模型原始长度和已截断事实；
4. 尽量保留 shell 的退出码、错误尾部和超时信息。

### FR-24：持久化输出上限

Session 可以保存比模型上下文更完整的工具结果，但必须设置单事件硬上限，避免异常命令生成无限大的日志。若持久化内容也被截断，必须记录原始长度和截断状态。

终端展示、模型观察和 Session 持久化可以使用不同上限，但三者必须来自同一个 ToolResult 事实。

## 14. 错误处理

### FR-25：模型协议错误

以下问题必须转化为模型可见的错误结果：

- 未注册或未激活的工具；
- tool arguments 不是合法 JSON；
- 必填参数缺失；
- 参数类型或取值不合法；
- tool call ID 缺失或冲突。

模型可在下一轮修正调用。系统必须配置连续协议错误上限，达到上限后终止当前 turn。

### FR-26：工具执行错误

以下情况应作为普通失败 ToolResult 返回，而不是直接终止 Agent：

- 文件不存在；
- 文本替换目标不唯一或不存在；
- 搜索无结果；
- shell 非零退出码；
- 命令超时；
- 用户拒绝授权。

未经处理的工具异常必须在工具边界被捕获、转换并记录。

### FR-27：未捕获异常

发生未捕获异常时，系统必须尽最大可能：

1. 写入 `error` 和 `session_end` 事件；
2. 保存异常类型、可公开的错误信息和诊断堆栈；
3. 将进程以非零状态退出；
4. 不吞掉导致开发者无法诊断的问题；
5. 不在错误信息中泄露凭据。

## 15. 运行限制

### FR-28：有界执行

每个 turn 必须受到以下限制，相关计数和剩余额度必须存放在当前 AgentRuntime 的 BudgetTracker 中：

- 最大模型轮数或 Agent step 数；
- 最大工具调用数；
- 最大连续协议错误数；
- 最大墙钟时间；
- 单条 shell 命令超时；
- 模型请求重试次数；
- 单次工具输出上限。

触发限制时必须记录具体原因，并尽可能让用户看到当前已完成工作，而不是只返回笼统错误。

## 16. 可见性与统计

### FR-29：终端事件展示

交互式 CLI 必须实时或准实时展示：

- assistant 文本；
- 工具名称和关键参数；
- 权限询问与决定；
- 工具成功、失败、退出码和耗时；
- 当前步骤或调用计数；
- compaction、重试、限制触发和最终状态。

大段文件内容和命令输出应折叠或截断，避免淹没关键状态。

### FR-30：运行统计

如果模型 API 返回 usage，系统应该记录：

- 输入 token；
- 输出 token；
- 模型调用次数；
- 工具调用次数；
- 运行总耗时。

费用统计可以在模型价格明确可配置后实现，不应硬编码可能过期的价格。

## 17. 架构扩展性约束

本节中的要求属于首版架构基线，但不要求首版实现 Docker 或子 Agent。

### FR-31：AgentRuntime 隔离

1. 每个 Agent 必须拥有独立的 AgentRuntime。
2. ContextState、BudgetTracker、active tools 和 CancellationSignal 必须归属于一个明确的 AgentRuntime，不得存放在模块级可变全局变量中。
3. AgentRuntime 必须具有稳定的 agent ID，并预留可选的 parent agent ID。
4. ExecutionEnvironment 和 ExecutionPolicy 必须通过 Runtime 明确提供，禁止工具从全局配置中隐式获取。
5. 首版 root Agent 同样必须使用 AgentRuntime，不得为了当前只有一个 Agent 而绕过该抽象。

### FR-32：ExecutionEnvironment 边界

1. 所有工具产生的文件、搜索和进程副作用必须经由 ExecutionEnvironment。
2. 首版必须实现 LocalExecutionEnvironment，并由其封装宿主机路径解析、文件操作、`rg` 调用和 subprocess 执行。
3. Environment 必须提供明确的启动、关闭、取消、超时和 workspace 语义。
4. Environment 返回值必须是与具体后端无关的结构化结果，使 ToolResult 不泄漏 Docker exec 等实现细节。
5. 新增符合相同契约的 DockerExecutionEnvironment 时，现有内置工具和 Agent Loop 原则上不应需要修改。

### FR-33：Agent Loop 可独立实例化

1. Agent Loop 的构造和运行必须显式接收 Model Client、Tool Registry、事件接口和 AgentRuntime 等依赖。
2. Agent Loop 不得使用全局当前会话、全局当前 workspace、全局 active tools 或全局计数器。
3. 在同一进程中创建两个使用不同 Runtime 的 Agent Loop 时，其上下文、预算、工具集合、取消状态和环境不得互相污染。
4. 首版可以只调度一个 Loop；未来串行子 Agent 可以通过创建 child Runtime 和新的 Loop 实例实现。

### FR-34：事件归属与调用关联

1. 所有事件必须包含 agent ID，包括当前只有 root Agent 时。
2. 每个事件必须具有稳定且唯一的 event ID。
3. Session 内的 sequence number 由 Session Store 统一分配；未来并发写入时仍必须保持单调和无重复。
4. 一次工具调用的 `tool_call`、`policy_decision`、`tool_result` 和相关错误必须共享同一个内部 correlation ID。
5. correlation ID 必须由本系统生成并持久化，不得依赖模型厂商 ID 的存在、格式或跨请求稳定性。
6. agent ID 表示事件归属，correlation ID 表示一次操作链路，两者不得混用。

### 17.1 未来升级边界

满足 FR-31 至 FR-34 后，预期升级路径如下：

| 能力 | 预期主要改动 | 当前不得预设的错误假设 |
| --- | --- | --- |
| Docker 隔离 | 新增 DockerExecutionEnvironment、生命周期与配置 | 不得让 Tool 直接访问宿主机作为兜底 |
| 串行子 Agent | 新增 `delegate_task`、child Runtime、预算与取消继承 | 不得让父子 Agent 共享 ContextState 或 active tools 可变对象 |
| 并行子 Agent | 新增调度器、工作区隔离、全局预算协调和结果合并 | 不得默认多个 Agent 可安全共享同一可写工作区 |

未来 child Runtime 默认应满足最小权限原则：其 active tools、预算和 ExecutionPolicy 不得比父 Runtime 更宽，除非用户显式授权。

并行子 Agent 仍需单独设计以下问题，本基线不声称已经解决：

- 共享只读工作区、Git worktree、复制目录或写时复制层之间的选择；
- 并发数、预算预留、父子取消传播和失败传播；
- 文件修改的 patch 化、冲突检测、合并顺序和回滚；
- 子 Agent 结果的压缩、可信度和注入父上下文的格式。

## 18. 非功能需求

### NFR-01：可理解性

核心执行链路必须能够通过少量模块和统一数据结构解释，不得为了形式上的扩展性引入工作流图、依赖注入框架或复杂插件生命周期。

### NFR-02：可测试性

Agent Loop、Model Client、工具、策略、Context Builder、ExecutionEnvironment 和 Session Store 必须可分别测试。Agent Loop 测试必须能够注入 fake model 和 fake environment，而无需真实调用外部 API 或操作宿主机工作区。

### NFR-03：可恢复性

关键事件应在产生后及时持久化。Session 文件损坏时，系统应至少能够识别最后一个有效事件并给出诊断。

### NFR-04：可移植性

系统以 macOS 和常见 Linux 环境为主要运行目标。对 `rg`、shell 等外部可执行文件的依赖必须检测并给出明确提示。

### NFR-05：安全性

1. 凭据不得持久化。
2. 文件工具必须执行 workspace 边界检查。
3. 权限策略异常必须 fail-closed。
4. 系统必须如实说明 shell 继承宿主用户权限，不夸大安全能力。

### NFR-06：可观察性

同一运行事件必须可以同时被 Session Store 和终端渲染器消费，核心 Agent Loop 不应直接依赖具体终端 UI。

### NFR-07：可扩展性

首版抽象必须服务于已确认的 Docker 和子 Agent 升级方向，但不得提前实现通用依赖注入容器、插件框架或分布式调度协议。新增 Environment 实现和创建 child Runtime 应当分别是环境替换与 Agent 组合的主要扩展点。

## 19. 首个版本明确不做

以下能力不属于本基线，除非核心需求完成后作为增强项加入：

- Session tree、branch、fork、clone；
- MCP；
- Skill 系统；
- RAG、Embedding 和向量数据库；
- 动态插件或第三方 extension 加载；
- `delegate_task`、子智能体和多智能体调度的具体实现；
- 后台命令与并行工具执行；
- 运行中异步 steering 或 follow-up queue；
- Plan mode、Todo 管理等独立工作模式；
- DockerExecutionEnvironment、容器或操作系统级沙箱的具体实现；
- 多模型厂商适配器；
- 完整 TUI；
- 服务端托管文件与代码执行；
- 以命令黑名单冒充可靠安全边界。

这些非目标不排除首版实现 AgentRuntime、ExecutionEnvironment、agent ID 和 correlation ID 等必要扩展边界。

## 20. 基线验收标准

首个可提交版本至少必须通过以下验收场景。

### AC-01：真实编程任务闭环

给定一个包含缺陷或待实现功能的小型仓库，Agent 能够：

1. 查看目录和相关代码；
2. 搜索目标符号或文本；
3. 修改一个或多个文件；
4. 执行测试或其他验证命令；
5. 根据失败结果至少进行一次合理修正；
6. 最终返回无 tool call 的结果总结。

### AC-02：结构化工具错误恢复

Fake model 或真实模型产生非法工具参数时，系统能返回结构化错误结果，允许模型修正，并在连续错误达到上限时有界终止。

### AC-03：工具执行失败恢复

shell 返回非零退出码或文件编辑失败时，Agent Loop 不崩溃，失败信息能够进入下一次模型上下文。

### AC-04：路径逃逸防护

针对 `..`、绝对路径和指向 workspace 外的符号链接，所有结构化文件工具均拒绝访问或写入。

### AC-05：权限策略

交互模式下 bash 默认请求确认；拒绝后产生对应 ToolResult；显式自动执行模式下可完成无人工干预的任务。

### AC-06：轨迹完整性

一次正常运行和一次异常中断运行都生成可解析 JSONL。轨迹能够还原用户消息、模型回复、工具调用、策略决定、工具结果和最终状态。

### AC-07：会话恢复

系统能够加载已有线性 session，并在不重放历史副作用的前提下接受新的 follow-up。

### AC-08：上下文压缩

在测试中使用较小上下文预算触发 compaction 后：

1. 原始 JSONL 历史仍然存在；
2. 新模型请求包含 summary 和最近完整交互；
3. tool call 与 tool result 未被拆散；
4. 会话能够继续完成任务。

### AC-09：执行限制

模型持续调用工具或持续产生非法输出时，系统在配置上限内停止，并记录准确的 `LIMIT_REACHED` 原因。

### AC-10：凭据保护

代码仓库、Session JSONL、错误日志和示例配置中均不包含真实 API Key。

### AC-11：Environment 替换边界

使用 fake ExecutionEnvironment 运行内置工具时，工具能够完成参数处理并生成 ToolResult，且测试期间不直接访问宿主机文件或进程。代码检查应确认只有 LocalExecutionEnvironment 等 Environment 实现包含实际文件和 subprocess 操作。

### AC-12：Runtime 状态隔离

在同一进程中创建两个 AgentRuntime 并分别运行 fake-model 场景后，两者的消息、压缩状态、预算计数、active tools、取消状态和 Environment 调用记录互不污染。

### AC-13：事件归属与关联

一次完整工具执行产生的所有事件均包含相同 agent ID；`tool_call`、`policy_decision` 和 `tool_result` 具有相同的内部 correlation ID；不同调用的 correlation ID 不重复，且模型厂商 tool-call ID 被独立保存。

### AC-14：LocalExecutionEnvironment 生命周期

LocalExecutionEnvironment 能够初始化逻辑 workspace、执行六类环境操作、响应取消或超时并可靠关闭；工具和 Agent Loop 不依赖其具体类型。

## 21. 推荐实施优先级

实现顺序不改变需求优先级，但建议按以下阶段降低集成风险：

1. **运行边界**：AgentRuntime、ExecutionEnvironment Protocol、LocalExecutionEnvironment、事件 envelope。
2. **核心闭环**：Model Client、可独立实例化的 Agent Loop、Tool Registry、ToolExecutionContext、六个内置工具。
3. **执行控制**：路径边界、Execution Policy、取消、超时、输出截断和运行限制。
4. **持久化**：带 agent ID 和 correlation ID 的标准事件、JSONL Session Store、异常时保存。
5. **上下文管理**：Runtime 独立的 Context Builder、预算估算、自动增量 compaction。
6. **交互体验**：交互式 CLI、非交互模式、终端渲染和 session 恢复。
7. **验证交付**：单元测试、fake-model/fake-environment 集成测试、真实任务演示、README.txt 和视频素材。

## 22. 需求变更规则

以下情况视为对本基线的变更，应先更新本文档再实施：

- 改变编程语言或主要模型协议；
- 移除六个基线工具之一；
- 将线性 Session 改为树形历史；
- 改变默认权限语义；
- 取消历史与模型上下文分离；
- 取消自动 compaction；
- 将“明确不做”的大型能力提升为首个版本必需项；
- 破坏 Tool 对 ExecutionEnvironment 的单向依赖；
- 将 AgentRuntime 状态改为进程级全局状态；
- 改变 ExecutionEnvironment 契约或系统所声明的安全模型；
- 引入新的 Agent 框架或远程执行控制平面。

新增符合现有契约的 ExecutionEnvironment 实现本身不视为核心架构变更，但将其提升为首版必需功能仍需更新范围和验收标准。小范围参数调整、终端样式优化、内部类名变化和不改变外部语义的重构，不视为需求变更。
