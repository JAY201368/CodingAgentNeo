# 后端历史发现 Agent 工作协议

`requirement.md` 是产品权威，`ARCHITECTURE.md` 控制技术边界和契约，`TASKS.md` 控制任务范围、依赖和验收。除非本工作流要求更严格，否则继承 `docs/baseline/AGENTS.md` 中仓库级 Agent 要求。

## 1. 开始工作前

1. 阅读本文件、`requirement.md`、`ARCHITECTURE.md`、`TASKS.md`、`PROGRESS.md`、`DECISIONS.md`、`../baseline/AGENTS.md`、所选任务及其全部已完成依赖。
2. 检查工作树并保留无关变更。
3. 不得假设未完成的依赖已经存在，也不得在范围外实现替代方案。
4. 一次只处理一个任务。修改公开契约前先更新架构和受影响的任务卡。
5. 编排器为每个任务 ID 分配一个全新的专用子代理。工作代理绝不能领取、验收或继续执行另一个任务。

## 2. 标准命令

| 命令 | 用途 |
| --- | --- |
| `python -m pip install -e ".[dev,http]"` | 安装开发和 HTTP 依赖，但不覆盖本地配置 |
| `python -m pytest` | 运行全部测试 |
| `python -m ruff check .` | 运行 lint，但不改写文件 |
| `python -m ruff format --check .` | 检查格式，但不改写文件 |
| `python -m build` | 构建 sdist 和 wheel |
| `python -m pytest tests/acceptance -m acceptance` | 运行本地聚合验收套件 |
| `python /Users/jay/.codex/skills/orchestrate-spec-driven-development/scripts/validate_workflow.py --repo docs/backend-history-discover` | 校验工作流结构 |

使用当前 Python 3.12 环境。格式修复必须只明确指定当前任务拥有的路径。

## 3. 目录与模块边界

- `docs/agent-backend-interface.md` 是提供者和单会话后端语义的权威；`docs/agent-transport-interface.md` 是前端绑定的权威。
- `session.py` 可以拥有规范固定目录的发现/解析原语。传输模块不得导入 `SessionStore`、调用 `read_session`、枚举路径或推导会话摘要。
- 工作区范围的提供者是适配器唯一的后端应用依赖。单会话 `AgentBackend` 只保留实时命令/事件生命周期。
- `assembly.py` 负责具体提供者构造以及新建/恢复会话的组装。它不得导入 CLI、渲染器、HTTP 或 Web 模块。
- `transports/in_process.py` 和 `transports/http/` 只映射公开契约。它们不得拥有历史、路径、恢复或 Agent 执行语义。
- `cli.py` 可以保留不透明 ID 形式的 `--resume`；但不得扫描历史，也不得接受会话目录/文件。`web/` 不属于本工作流任何任务的范围。

## 4. 代码与契约约定

- 目标 Python 版本为 3.12。公开边界使用带类型的冻结 dataclass、Protocol、稳定字符串错误码和 JSON 兼容值。
- 生产持久化位置始终为 `resolved_workspace / ".coding-agent-neo" / "sessions"`；不得增加别名、环境变量、隐藏回退或已弃用的公开覆盖项。
- 公开恢复输入是不透明 `session_id`，绝不是路径。构造路径前必须校验，不得递归或跟随符号链接，也绝不能返回/记录路径。
- 历史响应和文本必须有界并明确标记截断。一个损坏候选项不得使列表失败；直接的无效读取/恢复必须安全失败。
- 保持规范 EventEnvelope 模式和序列。历史读取分页是有限的；实时事件仍使用 SSE/迭代器。
- API 密钥、工作区路径、用户文本、原始 JSONL、回溯和提供者载荷不得进入安全错误或新增日志。

## 5. 最低验证要求

| 变更类型 | 最低验证 |
| --- | --- |
| 文档 | 链接、路径、命令、代码块和架构一致性 |
| 领域逻辑 | 成功、失败和边界的单元测试 |
| 公开契约 | 载荷、权限、状态和错误的集成测试 |
| JSONL/历史 | 边界、排序、损坏/尾部、符号链接/穿越、序列和不重放的单元测试 |
| 配置/CLI | 解析器/配置测试，以及子进程帮助、恢复和错误行为 |
| 适配器 | 共享一致性测试，以及特定绑定的映射和生命周期测试 |
| UI | 不在范围内；确认 `web/` 没有变更 |
| 部署 | 配置校验、健康检查、路由和持久化检查 |

## 6. 禁止事项

- 不得提交密钥、用户私有数据、凭据、备份或大型生成物。
- 未经明确授权，不得执行破坏性的仓库、数据库、存储或卷操作。
- 不得改写无关文件、降低质量门禁或把未经验证的行为报告为已完成。
- 不得修改 `web/`、迁移或删除现有会话文件、增加原始文件端点，或以另一个公开名称恢复 `session_dir`。
- 工作代理不得提交。只有主代理负责验收并提交每个已完成任务。

## 7. 交付报告

报告任务 ID 和可观察行为、变更模块、准确命令及结果、契约/迁移/配置影响和限制。按事实更新 `PROGRESS.md` 和持久性 `DECISIONS.md`，但不得勾选任务或提交。只有主代理可以独立验收、勾选并提交任务。
