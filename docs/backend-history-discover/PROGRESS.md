# 进度

## 已完成

- T01 — 后端和传输规范现已定义一个工作区范围的提供者、有界历史摘要/事件分页、固定路径的不透明 ID 恢复、等价的 In-process/HTTP 绑定以及稳定安全错误；结构测试和现有架构/传输测试均已通过。
- T02 — 生产配置和启动器不再暴露会话目录；新建/恢复的 JSONL 被限制在固定工作区仓库内，并采用不透明 ID 及符号链接/包含关系检查。
- T03 — 工作区范围的提供者、不可变历史 DTO/错误、私有固定目录发现、有界首条根消息/事件投影、不透明快照游标以及由提供者严格控制的新建/恢复创建均已验收。
- T04 — 规范的提供者支持型 In-process 工作区绑定和经提供者路由的兼容构建器已通过验收，并具备可复用的历史/恢复一致性测试。
- T05 — 仅依赖提供者的 HTTP 有限历史列表/读取及新建/恢复创建已通过验收，具备严格有界解码、稳定安全错误、恢复游标，并保留实时传输行为。
- T06 — 已对齐接口索引、README/配置指南、工作流证据和聚合提供者边界检查；完整仓库质量门禁已通过验收。

## 当前状态

- T01–T06 均已验收。后端历史发现工作流已经完成：In-process 和 HTTP 前端可以通过统一提供者契约发现、读取和恢复工作区会话。

## T06 集成里程碑报告

- 已验收的实现提交：`16ed3f2`（T01 契约）、`c359510`（T02 固定工作区存储）、`f7ea236`（T03 提供者/历史）、`84ddac2`（T04 In-process 绑定）和 `fd9f4c2`（T05 HTTP 绑定）。本次审计前已从仓库提交历史确认 T04 和 T05。
- 已验收系统只有一个生产持久化位置 `<workspace>/.coding-agent-neo/sessions/`，并使用一个工作区范围的 `AgentBackendProvider` 负责有界历史列表/读取以及新建/恢复单会话后端。历史读取使用有限 JSON；实时事件仍使用 SSE/迭代器。
- 兼容性影响：旧版自定义会话目录不会被迁移或发现。现有 CLI `--resume` 仍采用不透明会话 ID 流程；已移除的路径/配置输入会在校验阶段失败。兼容构建器仍经提供者路由，且不是适配器依赖。
- Web UI 工作被有意延期到本工作流之外；T06 未修改任何 `web/` 源码。
- 需求审计：产品历史列表/首条根消息投影由 `tests/unit/test_session_history.py` 覆盖；有限事件读取以及恢复序列/不重放由 `tests/transports/test_adapter_conformance.py` 和 `tests/integration/test_http_history.py` 覆盖；固定路径/配置/不透明 CLI 行为由 `tests/unit/test_config.py` 和 `tests/integration/test_cli.py`/`test_resume_cli.py` 覆盖；损坏、边界、符号链接、穿越、诊断及替换后重新校验由会话历史/提供者/安全测试覆盖；仅依赖提供者的适配器边界由 `tests/architecture/test_forbidden_dependencies.py` 及其聚合验收条目强制执行。未引入原始文件路由或 Web 源码变更。
- 质量门禁（HTTP 验收测试使用环回代理绕过）：`.venv/bin/python -m pytest` — 342 项通过，1 条第三方 `StarletteDeprecationWarning`；`.venv/bin/python -m pytest tests/acceptance -m acceptance` — 56 项通过，1 条相同警告；`.venv/bin/python -m ruff check .` — 通过；`.venv/bin/python -m ruff format --check .` — 122 个文件已格式化；`.venv/bin/python -m build` — 已构建 `coding_agent_neo-0.1.0.tar.gz` 和 `coding_agent_neo-0.1.0-py3-none-any.whl`；工作流校验器 — `OK workflow structure is valid (6 tasks)`；`git diff --check` — 通过。
- 所列门禁不存在仅由环境导致的阻塞。唯一警告是已安装测试依赖发出的第三方 Starlette/httpx 弃用警告。

## 已知问题

- 自定义目录中的现有会话文件不会自动迁移。
- Web UI 消费被有意延期，`web/` 不在本工作流范围内。
- 裸系统 Python 由 PEP-668 管理且缺少 `pytest`；所有必需门禁均在项目本地 `.venv` 中运行，其中包含已记录的开发/HTTP 额外依赖，HTTP 验收测试使用环回代理绕过。
- T05 证据：主代理 HTTP/历史/安全矩阵通过（34 项），Web 启动器/验收回归通过（11 项），包含真实 HTTP 历史的可复用适配器一致性测试通过（5 项），Ruff 检查/格式检查、工作流校验和 `git diff --check` 均通过；工作代理完整测试套件通过（340 项）。

## 下一推荐任务

- 无。所有工作流任务均已验收；Web UI 历史消费仍被有意保留在本工作流之外。
