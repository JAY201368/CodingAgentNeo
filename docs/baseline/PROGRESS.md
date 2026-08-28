# CodingAgentNeo 当前进度

> 更新日期：2026-08-28
> 当前阶段：Execute — T02 已接受，等待后续授权

## Completed

- T01 — 已建立可编辑安装的 Python 3.12 项目骨架、两种 CLI help 入口、标准质量门、无凭据示例配置和受控忽略规则；主 Agent 已独立复核全部验收证据。
- T02 — 已交付后端无关数据模型、Runtime 隔离状态、预算/取消信号、Environment Protocol、ToolExecutionContext、事件信封、可注入 ID/时钟和无宿主副作用的 fake environment；主 Agent 独立复验定向测试 17 passed、全量 pytest 19 passed 及全部静态质量门。

## Current State

- 用户已指定 `docs/agent-system-requirements-baseline.md` v1.1 为权威需求正文。
- 开发过程文档已统一迁移到 `docs/baseline/`，并于 2026-08-28 通过用户审阅。
- 仓库现有 `pyproject.toml`、`src/coding_agent_neo/`、测试分层目录、示例配置和开发 README；CLI 目前只提供明确标注为未实现的公共帮助入口，Agent、模型、工具、环境和会话行为仍未实现。
- T01、T02 均通过各自专用 worker 验证和主 Agent 独立复验；Local I/O、JSONL、模型访问、工具执行和 Agent Loop 仍未实现。

## Known Issues

- 尚无真实模型调用、LocalExecutionEnvironment、安全边界或完整验收场景的运行证据；T02 仅固化契约和 fake environment，不实现宿主机 I/O。
- T13 涉及公开仓库、视频与外部提交的人工/授权步骤，不能由自动化测试单独证明。
- 宿主 Homebrew Python 的直接安装命令受 PEP 668 限制；在 Python 3.12 `.venv` 中执行同一安装命令并完成全部 T01 质量门。

## Next Recommended Task

- 按推荐顺序，T03（受 workspace 约束的 LocalExecutionEnvironment）是下一任务；T04、T06、T07 也已依赖就绪，但均尚未获后续派发授权。
