# CodingAgentNeo Agent 工作协议

`requirement.md` 指向产品需求权威正文；`ARCHITECTURE.md` 控制技术边界和公开契约；`TASKS.md` 控制任务范围、依赖和验收。用户尚未审阅当前文档基线，因此现在禁止启动 T01 或任何实现任务。

## 1. 开始工作前

1. 阅读本文件、`requirement.md` 及其链接的完整需求、`ARCHITECTURE.md`、`PROGRESS.md`、当前完整任务卡和所有已完成依赖的总结。
2. 检查 `git status` 与相关文件；现有改动属于用户或其他 Agent，保留无关改动，发生范围重叠时先报告。
3. 从仓库证据确认依赖已经 `[x]` 且有验证结果；不得假设未完成依赖存在，也不得在当前范围内偷偷实现替代品。
4. 先用一句话复述当前任务范围和排除项，再修改文件。一次只处理一个任务 ID。
5. 每个任务 ID 必须由编排器创建一个全新的专用子 Agent。Worker 不得领取、开始或继续后续任务；任务接受、放弃或阻塞后关闭该上下文。
6. 公开接口、数据模型、状态机、安全边界或部署契约不完整时停止扩展，先更新架构、任务和必要的决策记录。

## 2. 标准命令

以下是 T01 必须建立的目标命令。T01 尚未完成，因此当前不得报告它们已可用或已通过。

| 命令 | 用途 |
| --- | --- |
| `python -m pip install -e ".[dev]"` | 安装开发依赖，不覆盖本地配置 |
| `python -m coding_agent_neo --help` | 检查模块 CLI 入口 |
| `python -m pytest` | 运行全部自动化测试 |
| `python -m ruff check .` | lint，不改写文件 |
| `python -m ruff format --check .` | 格式检查，不改写文件 |
| `python -m build` | 构建 sdist/wheel |
| `python -m pytest tests/acceptance -m acceptance` | 运行本地聚合验收；真实 API 场景按 runbook 单独执行 |

格式修复只针对当前任务文件使用 `python -m ruff format <明确路径>`，不得借机全仓机械重写。新增标准命令须先更新本节和相关任务。

## 3. 目录与模块边界

- 产品代码计划位于 `src/coding_agent_neo/`，测试位于 `tests/unit/`、`tests/integration/`、`tests/security/`、`tests/architecture/`、`tests/acceptance/`；具体模块所有权以 `ARCHITECTURE.md` 第 4 节为准。
- 只有 `environment/local.py` 可以实际读写宿主文件、调用 `rg` 或启动 subprocess。Tool、Agent Loop 和 Context 不得直接使用这些能力。
- `runtime.py` 拥有每 Agent 可变状态；禁止模块级 current agent/session/workspace/active tools/budget。
- `session.py` 拥有 append-only 事实历史；`context.py`/`compactor.py` 只能生成投影，不能删除或改写历史。
- `model_client.py` 隔离厂商协议；其他模块只接收归一化内部模型。
- `cli.py` 只组装依赖和处理终端 I/O，不复制 Agent 决策、安全校验或持久化逻辑。

## 4. 代码与契约约定

- 目标运行版本 Python 3.12；公共函数和边界对象必须有类型标注。使用小型 dataclass/enum/Protocol，避免 DI 框架、工作流图或插件生命周期。
- 命名使用 `snake_case`，类使用 `PascalCase`，常量使用 `UPPER_SNAKE_CASE`；事件类型、工具名、CLI 选项和 JSON 字段一经公开必须与架构一致。
- 时间戳为 UTC ISO 8601，持续时间/超时使用单调时钟；ID 生成器和时钟必须可注入测试。
- 每个 tool call 恰好一个 ToolResult；普通工具错误不得穿透 Loop。correlation ID、provider tool-call ID 和 agent ID 不得混用。
- 路径安全由 Environment 最终执行；Local bash 必须明确是宿主权限命令而非沙箱。策略异常 fail-closed。
- 配置覆盖为 CLI > 环境变量 > 未入库 TOML > 默认值。API Key 只按环境变量名读取；禁止把 key 值放进 CLI argv、代码、fixture、snapshot、日志、JSONL、README 或视频。
- 测试优先 fake model/fake environment；mock 只能证明本项目逻辑，不能声称真实 API、shell 隔离或跨平台已验证。

## 5. 变更类型与最低验证

| 变更类型 | 最低验证 |
| --- | --- |
| 仅流程/架构文档 | 链接、路径、Mermaid/code block、需求追踪、任务 DAG 和 skill validator；不得运行产品验收冒充实现 |
| 数据模型/Runtime | 成功、非法值、mutable isolation、序列化单测 + Ruff |
| Environment/文件/进程 | 临时目录组件测试、逃逸/symlink/timeout/cancel 安全测试 + 禁止依赖审查 |
| Tool/Policy/Executor | schema、active/unknown、allow/ask/deny/fail-closed、恰好一个结果、ID 关联测试 |
| Model 协议 | mock transport 的请求/响应/错误分类/重试/脱敏测试；若改真实兼容声明则补真实网关证据 |
| Agent Loop/Context | scripted model 集成测试，覆盖成功、失败修正、限制、中断、压缩和 Runtime 隔离 |
| JSONL/恢复 | append/flush、截断、损坏尾部、sequence、无副作用重放测试 |
| CLI/配置/渲染 | 子进程集成测试、覆盖顺序、退出码、stdin/approval/Ctrl+C、大输出展示 + build |
| 交付物 | 全量质量门、脱敏真实任务 runbook、README 字数、视频时长/大小和人工清单 |

只运行最小矩阵不足以接受任务时，以任务卡更严格的 Verification 为准。命令未运行、跳过或因环境失败时必须原样报告。

## 6. 禁止事项

- 不得提交 secret、真实 session、私有数据、本地配置、备份、虚拟环境或大生成物。
- 未经用户明确授权，不得执行 destructive git/filesystem 命令、改写已推送历史、发布仓库、推送远端、录屏或外部提交。
- 不得绕过 Environment、approval、预算、事件落盘或测试质量门；不得以黑名单宣称 shell 安全。
- 不得修改无关文件、扩大当前卡片、顺手完成下一个任务、降低验收或把“未运行”写成“通过”。
- 不得在依赖未完成、架构不一致或用户仍在审阅本基线时开始产品实现。

## 7. 交付报告与勾选条件

Worker 最终报告必须列出：任务 ID 与范围复述、变更文件/模块、可观察行为、实际命令及逐项结果、Acceptance checklist、契约/迁移/配置/下游影响、限制或阻塞，并按事实更新 `PROGRESS.md`；只有形成持久且非显然的选择时才追加 `DECISIONS.md`。

主 Agent 必须独立审阅 diff 和证据。全部验收满足后，才在任务卡勾选并追加日期、行为、边界和真实验证结果；随后关闭该任务的专用 Agent，再决定是否按用户授权继续。
