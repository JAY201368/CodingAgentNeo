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
