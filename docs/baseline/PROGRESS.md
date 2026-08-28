# CodingAgentNeo 当前进度

> 更新日期：2026-08-28
> 当前阶段：Execute — T05 已接受，等待后续授权

## Completed

- T01 — 已建立可编辑安装的 Python 3.12 项目骨架、两种 CLI help 入口、标准质量门、无凭据示例配置和受控忽略规则；主 Agent 已独立复核全部验收证据。
- T02 — 已交付后端无关数据模型、Runtime 隔离状态、预算/取消信号、Environment Protocol、ToolExecutionContext、事件信封、可注入 ID/时钟和无宿主副作用的 fake environment；主 Agent 独立复验定向测试 17 passed、全量 pytest 19 passed 及全部静态质量门。
- T03 — 已交付 workspace 约束的 `LocalExecutionEnvironment`，覆盖六类环境操作、真实路径/待创建父目录边界、symlink 逃逸拒绝、结果截断、`rg` 检测与标准库退化、协作式取消、命令超时和关闭回收；另已修复 POSIX shell leader 提前退出时的后台 descendant 回收。主 Agent 独立复验定向组件/安全测试 11 passed、全量 pytest 30 passed、延迟 marker 对抗场景、Ruff lint/format、build、workflow validator 与 `git diff --check` 均通过。
- T04 — 已交付 `Tool` Protocol、JSON 参数 schema/结构化协议错误、注册与 active 分离、六个内置工具及统一 `ToolResult` 归一化、模型/持久化输出投影；工具仅通过 `ToolExecutionContext.environment` 传递请求和取消信号。主 Agent 验收时退回修复了未激活 schema 暴露缺口，并独立复验定向测试 14 passed、全量 pytest 44 passed、Ruff lint/format、build、workflow validator、禁止依赖静态扫描与 `git diff --check` 均通过。
- T05 — 已交付 fail-closed `DefaultExecutionPolicy`、交互/非交互 approval port、`ToolExecutor` 单调用生命周期和后端无关最小事件发布协议；覆盖 workspace 相对路径安全、bash ask/auto/yolo/deny、策略/审批异常拒绝、correlation/provider ID 关联、校验/拒绝/普通失败/意外异常归一化及恰好一个 `ToolResult`/事件。主 Agent 验收时退回修复了敏感诊断泄漏和 event ID 注入缺口，并独立复验定向测试 24 passed、全量 pytest 68 passed、对抗脚本、Ruff lint/format、build、workflow validator、依赖边界扫描与 `git diff --check` 均通过。

## Current State

- 用户已批准 `docs/agent-system-requirements-baseline.md` v1.2 为权威需求正文：首版只保留显式 system prompt 和通用 Tool 两个窄扩展边界，不实现 Skill/MCP 具体功能。
- 开发过程文档已统一迁移到 `docs/baseline/`，并于 2026-08-28 通过用户变更审阅；已完成 T01～T05 的范围和实现证据保持不变。
- 仓库现有 `pyproject.toml`、`src/coding_agent_neo/`、测试分层目录、示例配置和开发 README；CLI 目前只提供明确标注为未实现的公共帮助入口，模型、会话和 Agent Loop 行为仍未实现；工具系统已完成 T04，策略/执行器已完成 T05，T06/T07 尚未实现。
- T01、T02、T03、T04、T05 均已通过各自专用 worker 验证和主 Agent 独立复验；T06、T07 已依赖就绪，但均尚未获后续派发授权。

## Known Issues

- 尚无真实模型调用、完整 Agent Loop 或跨平台 Environment 运行证据；当前 LocalEnvironment 组件证据来自 macOS Python 3.12.11，shell 仍继承启动用户权限，不是操作系统沙箱。
- 尚无且首版不要求 Skill 目录发现/解析/加载或 MCP 客户端/配置/传输证据；后续 T08/T09 的 fake Tool 和显式 prompt 测试只验证核心边界，不代表这两项集成已完成。
- T13 涉及公开仓库、视频与外部提交的人工/授权步骤，不能由自动化测试单独证明。
- 宿主 Homebrew Python 的直接安装命令受 PEP 668 限制；在 Python 3.12 `.venv` 中执行同一安装命令并完成全部 T01 质量门。

## Next Recommended Task

- T06（append-only Session Store 与事件扇出）是下一推荐任务；T07 仍依赖就绪但须等待后续授权。
