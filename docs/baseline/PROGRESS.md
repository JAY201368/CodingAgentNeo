# CodingAgentNeo 当前进度

> 更新日期：2026-08-28
> 当前阶段：Execute — T01 已接受，等待后续授权

## Completed

- T01 — 已建立可编辑安装的 Python 3.12 项目骨架、两种 CLI help 入口、标准质量门、无凭据示例配置和受控忽略规则；主 Agent 已独立复核全部验收证据。

## Current State

- 用户已指定 `docs/agent-system-requirements-baseline.md` v1.1 为权威需求正文。
- 开发过程文档已统一迁移到 `docs/baseline/`，并于 2026-08-28 通过用户审阅。
- 仓库现有 `pyproject.toml`、`src/coding_agent_neo/`、测试分层目录、示例配置和开发 README；CLI 目前只提供明确标注为未实现的公共帮助入口，Agent、模型、工具、环境和会话行为仍未实现。
- T01 已通过 worker 验证和主 Agent 独立复验；T02 与 T07 之外的产品能力仍未实现。

## Known Issues

- 尚无产品测试、真实模型调用、LocalExecutionEnvironment、安全边界或验收场景的运行证据。
- T13 涉及公开仓库、视频与外部提交的人工/授权步骤，不能由自动化测试单独证明。
- 宿主 Homebrew Python 的直接安装命令受 PEP 668 限制；在 Python 3.12 `.venv` 中执行同一安装命令并完成全部 T01 质量门。

## Next Recommended Task

- T02（Runtime、Environment 与事件领域契约）和 T07（OpenAI-compatible 模型访问）依赖已就绪；本轮用户只授权 T01，尚未派发任何后续任务。
