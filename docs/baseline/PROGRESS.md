# CodingAgentNeo 当前进度

> 更新日期：2026-08-29
> 当前阶段：Execute — T07 已接受，等待后续授权

## Completed

- T01 — 已建立可编辑安装的 Python 3.12 项目骨架、两种 CLI help 入口、标准质量门、无凭据示例配置和受控忽略规则；主 Agent 已独立复核全部验收证据。
- T02 — 已交付后端无关数据模型、Runtime 隔离状态、预算/取消信号、Environment Protocol、ToolExecutionContext、事件信封、可注入 ID/时钟和无宿主副作用的 fake environment；主 Agent 独立复验定向测试 17 passed、全量 pytest 19 passed 及全部静态质量门。
- T03 — 已交付 workspace 约束的 `LocalExecutionEnvironment`，覆盖六类环境操作、真实路径/待创建父目录边界、symlink 逃逸拒绝、结果截断、`rg` 检测与标准库退化、协作式取消、命令超时和关闭回收；另已修复 POSIX shell leader 提前退出时的后台 descendant 回收。主 Agent 独立复验定向组件/安全测试 11 passed、全量 pytest 30 passed、延迟 marker 对抗场景、Ruff lint/format、build、workflow validator 与 `git diff --check` 均通过。
- T04 — 已交付 `Tool` Protocol、JSON 参数 schema/结构化协议错误、注册与 active 分离、六个内置工具及统一 `ToolResult` 归一化、模型/持久化输出投影；工具仅通过 `ToolExecutionContext.environment` 传递请求和取消信号。主 Agent 验收时退回修复了未激活 schema 暴露缺口，并独立复验定向测试 14 passed、全量 pytest 44 passed、Ruff lint/format、build、workflow validator、禁止依赖静态扫描与 `git diff --check` 均通过。
- T05 — 已交付 fail-closed `DefaultExecutionPolicy`、交互/非交互 approval port、`ToolExecutor` 单调用生命周期和后端无关最小事件发布协议；覆盖 workspace 相对路径安全、bash ask/auto/yolo/deny、策略/审批异常拒绝、correlation/provider ID 关联、校验/拒绝/普通失败/意外异常归一化及恰好一个 `ToolResult`/事件。主 Agent 验收时退回修复了敏感诊断泄漏和 event ID 注入缺口，并独立复验定向测试 24 passed、全量 pytest 68 passed、对抗脚本、Ruff lint/format、build、workflow validator、依赖边界扫描与 `git diff --check` 均通过。
- T06 — 已交付 Store-first `EventEmitter`、统一 sequence/event ID 管理、append-only UTF-8 JSONL `SessionStore`、逐事件 flush/默认 fsync、有界 payload 头尾预览、安全 JSON 化/递归敏感字段脱敏、同步订阅扇出与逐订阅者结果报告，以及完整记录读取和损坏尾行定位；主 Agent 验收时修复了 usage token 计数被过度脱敏的问题。独立复验定向测试 19 passed、T02/T05 回归 41 passed、全量 pytest 87 passed，并通过凭据/截断/重开/部分失败对抗脚本、Ruff lint/format、build、workflow validator 与 `git diff --check`。
- T07 — 已交付同步 `ModelClient` Protocol 与 OpenAI-compatible Chat Completions 适配器；请求明确传递 messages/active tool schemas/parameters，官方 OpenAI client 默认关闭其内部重试并支持注入 client、httpx transport 或 fake transport。新增无内部 correlation ID 的 `NormalizedAssistantResponse`/`NormalizedToolCall`/`NormalizedUsage`，保留合法 provider tool-call ID、非敏感 JSON arguments 原文和调用顺序、usage 与 finish reason，并将缺失/重复 ID、非法 arguments 和缺失响应归一化为安全诊断。网络/超时、429 与选定状态码使用可注入 sleep/clock 的有界指数退避；认证、权限、模型、配置和 context overflow 分别分类且不误重试；异常、日志和归一化文本/参数按安全边界脱敏，不保留 provider response 或 headers。主 Agent 独立复验 T07 定向测试 25 passed、T02/T05 回归 41 passed、全量 pytest 112 passed，并通过 Ruff lint/format、官方 SDK + `httpx.MockTransport` 的 429/401/context overflow/凭据对抗脚本、build、workflow validator 与 `git diff --check`；未执行真实网关调用。

## Current State

- 用户已批准 `docs/agent-system-requirements-baseline.md` v1.2 为权威需求正文：首版只保留显式 system prompt 和通用 Tool 两个窄扩展边界，不实现 Skill/MCP 具体功能。
- 开发过程文档已统一迁移到 `docs/baseline/`，并于 2026-08-28 通过用户变更审阅；已完成 T01～T07 的范围和实现证据保持不变。
- 仓库现有 `pyproject.toml`、`src/coding_agent_neo/`、测试分层目录、示例配置和开发 README；CLI 目前只提供明确标注为未实现的公共帮助入口，Agent Loop 行为仍未实现；工具系统已完成 T04，策略/执行器已完成 T05，事件/Session Store 已完成 T06，模型访问与重试已完成 T07。
- T01、T02、T03、T04、T05、T06、T07 均已通过各自专用 worker 验证和主 Agent 独立复验。

## Known Issues

- 尚无真实模型调用、完整 Agent Loop 或跨平台 Environment 运行证据；当前 LocalEnvironment 组件证据来自 macOS Python 3.12.11，shell 仍继承启动用户权限，不是操作系统沙箱。
- 尚无且首版不要求 Skill 目录发现/解析/加载或 MCP 客户端/配置/传输证据；后续 T08/T09 的 fake Tool 和显式 prompt 测试只验证核心边界，不代表这两项集成已完成。
- T13 涉及公开仓库、视频与外部提交的人工/授权步骤，不能由自动化测试单独证明。
- 宿主 Homebrew Python 的直接安装命令受 PEP 668 限制；在 Python 3.12 `.venv` 中执行同一安装命令并完成全部 T01 质量门。

## Next Recommended Task

- T08（交付可独立实例化、有界的 Agent Loop）是下一推荐任务，但尚未获得派发授权；本轮按用户要求止于 T07。
