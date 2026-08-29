# CodingAgentNeo 决策日志

本文件按时间追加持久且非显然的选择，不记录普通编辑或未经验证的完成声明。初始规划决策已于 2026-08-28 通过用户审阅；若被修改，应追加明确的 supersede 记录而不是改写历史语义。

## 2026-08-27 — WF 以单一需求正文接入根目录工作流

- 选择：保留 `docs/agent-system-requirements-baseline.md` v1.1 为唯一权威需求正文，根目录 `requirement.md` 只作为明确链接和优先级说明。
- 理由与替代：skill 校验器要求根目录入口；复制 768 行正文会制造双份需求和漂移风险，移动原文件会破坏用户指定路径。
- 后果：所有 worker 必须读取链接正文；需求变更只修改权威正文，再同步架构和任务。

## 2026-08-27 — ARCH 使用 Python src-layout 与 Chat Completions 兼容面

- 选择：首版计划使用 Python 3.12、`pyproject.toml`、`src/coding_agent_neo/`、官方 `openai` 传输客户端和 OpenAI-compatible Chat Completions 原生 tool calling。
- 理由与替代：Python 3.12 是已确认要求；src-layout 强化包边界；Chat Completions 比厂商专属 Responses 语义更适合单一兼容网关。直接手写 HTTP 可减少依赖但会扩大传输层工作，Agent SDK 则被需求禁止。
- 后果：T01 固化打包/质量命令，T07 隔离唯一厂商适配层；Agent Loop、重试分类、工具语义和上下文仍由本项目实现。改变主要模型协议须走需求变更。

## 2026-08-27 — ARCH 首版采用同步串行 Loop 并显式保留 Runtime/Environment 边界

- 选择：首版每进程一个前台同步 Agent Loop，多 tool calls 严格串行；每 Agent 可变状态放入 `AgentRuntime`，所有副作用经 `ExecutionEnvironment`。
- 理由与替代：这与单 Agent 首版范围和可解释性目标一致；提前引入 async 调度、子 Agent 或 Docker 会增加未被首版验收要求的并发与隔离复杂度。
- 后果：取消和超时仍必须出现在接口与测试中；未来子 Agent 或 Docker 通过新 Runtime/Environment 组合扩展，不得让现有 Tool 或 Loop 依赖具体后端。

## 2026-08-28 — WF 流程文档统一迁移至 docs/baseline

- 选择：用户将七份开发过程文档统一放置在 `docs/baseline/`，并批准该文档基线进入 Execute 模式。
- 理由与替代：集中保存基线文档可保持仓库根目录简洁；继续在根目录保留副本会产生双份流程状态。
- 后果：所有 worker 和派发提示必须使用 `docs/baseline/` 路径；skill 结构校验以 `--repo docs/baseline` 运行，权威需求正文仍为 `docs/agent-system-requirements-baseline.md`。

## 2026-08-28 — T02 固化带语义 ID 的事件与后端无关环境模型

- 选择：对内部 `AgentId`、`SessionId`、`EventId` 和 `CorrelationId` 使用可验证的字符串子类型；`ProviderToolCallId` 单独作为不透明外部字符串，仅拒绝空值、NUL 和非字符串并原样保留。`EventEnvelope` 将时间戳规范化为带 `Z` 的 UTC ISO 8601 字符串，并通过显式 ID factory/clock 提供测试注入点。六类环境请求/结果只表达逻辑路径、命令、限制和结构化状态，具体后端不进入模型。
- 理由与替代：字符串运行时兼容 JSONL，同时为 agent 归属和内部调用链保留可审查的类型边界；厂商 ID 的格式由兼容网关决定，不能套用内部字符集规则。直接暴露 `datetime`、宿主绝对路径或 Docker 元数据会增加序列化和后端耦合。时间/内部 ID 默认实现无共享可变状态，测试可使用确定性来源。
- 后果：后续 Session/Event 持久化可直接使用 `EventEnvelope.to_dict()`；Local 或 Docker 实现只能解释这些逻辑请求并返回统一结果，不能改变 Tool/Loop 的公开模型。

## 2026-08-28 — T03 LocalEnvironment 的搜索退化与边界语义

- 选择：`LocalExecutionEnvironment` 启动时检测 `rg`；若不可用或在调用间消失，`search` 使用明确标注 `engine=stdlib`、`fallback=python` 的标准库退化实现。递归 list/search 跳过解析到 workspace 外的 symlink，且所有请求路径均拒绝绝对路径、`..` 和 Windows drive 路径。
- 理由与替代：`rg` 是可选依赖，标准库退化可保持搜索可用且不把“无结果”误报为空成功；真实路径检查和不跟随外部链接可避免逻辑路径绕过。拒绝外链条目比仅列出链接名更便于调用方安全解析返回路径。
- 后果：搜索结果携带引擎/退化元数据；文件/列表/搜索/命令输出均有明确截断标记和原始长度；Local shell 仅保证初始 cwd 在 workspace，仍继承宿主用户权限，不提供沙箱隔离。

## 2026-08-28 — T04 工具注册激活与输出投影边界

- 选择：`ToolRegistry` 保存完整 registered set 与显式 active set；只有 active 工具生成 OpenAI-compatible function schemas 并允许 dispatch。六个内置工具共享后端无关请求/结果转换，参数协议错误在 Environment 调用前归一化为 `ToolResult(INVALID)`。
- 理由与替代：注册与激活分离可让运行时按 Runtime 权限投影工具，同时避免未激活工具误执行；统一结果边界让模型看到未知工具、非法 JSON、缺字段、类型错误和 Environment 普通失败的同一结构，而不把异常穿透 Loop。未引入动态插件或真实 Local 依赖，符合首版边界。
- 后果：模型/持久化输出均由同一 ToolResult 派生，超限保留头尾并记录 `original_length`；T05 可在 Registry 之上接入策略与执行生命周期，既有 Tool API 不直接承担 approval。

## 2026-08-28 — T05 策略与事件发布保持 fail-closed、可适配

- 选择：默认策略只对已验证且语法安全的 workspace 相对文件参数 allow，bash 按 ask/auto(yolo)/deny 决策；策略或 approval 异常统一 deny。`ToolExecutor` 为每次调用维持唯一内部 correlation ID，并通过不负责 sequence/持久化的最小 `ToolLifecycleEvent` 发布端口输出 `tool_call`、`policy_decision`、`tool_result`，事件 ID 使用 Runtime 的可注入 factory。
- 理由与替代：路径真实边界仍必须由 Environment 最终执行；命令黑名单不能成为安全边界。T06 尚未实现，若 T05 提前拥有 sequence、JSONL 或订阅扇出会造成职责倒置；完全不发布事件又无法独立验证调用关联和审批轨迹。
- 后果：T06 需把 lifecycle event 适配为分配 sequence 的 `EventEnvelope` 并负责扇出/持久化；T08 可直接复用同一 Executor。未注册名称、原始参数键和自定义 validator path 等不受信任诊断不得原样进入结果或事件。

## 2026-08-28 — ARCH Skill/MCP 只保留窄扩展边界

- 选择：将权威需求更新为 v1.2，只要求 system prompt 由组装层显式传入，以及 Registry、Executor 和 Loop 对符合协议的 Tool 来源无感。首版仍不实现 Skill 发现/解析/加载、MCP 客户端/配置/传输，也不引入通用资源管理器或插件框架。
- 理由与替代：Pi 的小核心思路表明 Skill 属于上下文组装，MCP 可以适配为 Tool；为两者预先建立具体模块、生命周期或配置面会扩大首版范围并降低可解释性。
- 安全边界：工作区文件、搜索和通用命令仍只经 `ExecutionEnvironment`。未来显式外部 Tool adapter 可拥有其协议必需的专用传输，但不得成为任意宿主文件、通用 shell、凭据泄漏或 Policy/事件绕过通道。
- 后果：T01～T05 保持已接受且无代码返工；T08 用注入的非内置 fake Tool 证明 Loop 来源无感，T09 证明显式 system prompt 被预算与 compaction 完整保留，但这些 fake 证据不得表述为 Skill/MCP 已集成。本决策对“v1.1 是当前版本”的历史状态作出 supersede，但不改变单一权威需求正文的文档策略。

## 2026-08-29 — T06 Store-first 事件序列化与安全扇出

- 选择：`SessionStore` 作为 `EventEmitter` 的唯一首要订阅者，负责分配 sequence、安全 JSON 化、payload 按 UTF-8 字节上限替换为带原始长度的头尾预览，并以 append + flush + 默认 `fsync` 完成后返回 canonical `EventEnvelope`；随后其他订阅者只消费这一 envelope。单个非 Store 订阅者失败时继续尝试其余订阅者，最后用不含异常消息的聚合报告区分 success/failed；Store 失败时因无 canonical sequence，后续订阅者显式标记 skipped。
- 理由与替代：由 Emitter 或多个订阅者各自分配 sequence 会产生两个事实源；在 Store 成功前渲染未持久化事件会让 UI 与审计轨迹分叉。未知厂商对象统一替换为固定占位符，而不调用其 `str`/`repr`；这比通用 `default=str` 更能防止 SDK 对象或异常携带凭据落盘。
- 后果：T05 `ToolLifecycleEvent` 可不修改 Executor 直接适配；T08/T10 只需注入同一 Emitter。订阅失败不会被伪装为全部成功，但 Store 已成功的事实也不因 renderer 失败而回滚；已损坏的尾行只诊断、不自动修复，业务恢复仍属于 T11。

## 2026-08-29 — T07 模型传输重试与归一化边界

- 选择：`OpenAICompatibleModelClient` 使用官方 OpenAI Chat Completions client 作为唯一 SDK 边界，并以 `max_retries=0` 关闭 SDK 内部重试；项目自身只对网络/超时、429、408/500/502/503/504 做可注入且最多配置次数的指数退避。认证、权限、模型不存在、其他 5xx、非法请求和配置错误立即失败；上下文窗口超限单独标为 `context_overflow` 交由后续 Context/Loop 决策。
- 理由与替代：把重试计数集中在项目层可解释且避免 SDK 与项目策略叠加；只选择可明确视为瞬时故障的状态码，避免对模型/请求错误盲目重试。模型响应使用无内部 correlation ID 的 `Normalized*` DTO，合法 provider ID 和原始 JSON arguments 保持顺序并把坏 ID/参数转为稳定诊断，让后续协议边界生成内部 correlation ID。
- 安全后果：只把稳定分类、状态码和安全诊断写入异常/日志；不保留 provider response、headers 或原始 SDK exception。文本和 arguments 在归一化边界按字段/inline 规则脱敏，未知对象不调用 `str`/`repr`；`httpx.MockTransport`/fake client 仅证明本项目离线逻辑，不证明真实网关兼容。
