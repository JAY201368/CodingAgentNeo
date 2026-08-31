# CodingAgentNeo 当前进度

> 更新日期：2026-08-31
> 当前阶段：Change control 已纳入 — T12 已接受，下一个依赖就绪任务为 T13（不含录制介绍视频）

## Completed

- T01 — 已建立可编辑安装的 Python 3.12 项目骨架、两种 CLI help 入口、标准质量门、无凭据示例配置和受控忽略规则；主 Agent 已独立复核全部验收证据。
- T02 — 已交付后端无关数据模型、Runtime 隔离状态、预算/取消信号、Environment Protocol、ToolExecutionContext、事件信封、可注入 ID/时钟和无宿主副作用的 fake environment；主 Agent 独立复验定向测试 17 passed、全量 pytest 19 passed 及全部静态质量门。
- T03 — 已交付 workspace 约束的 `LocalExecutionEnvironment`，覆盖六类环境操作、真实路径/待创建父目录边界、symlink 逃逸拒绝、结果截断、`rg` 检测与标准库退化、协作式取消、命令超时和关闭回收；另已修复 POSIX shell leader 提前退出时的后台 descendant 回收。主 Agent 独立复验定向组件/安全测试 11 passed、全量 pytest 30 passed、延迟 marker 对抗场景、Ruff lint/format、build、workflow validator 与 `git diff --check` 均通过。
- T04 — 已交付 `Tool` Protocol、JSON 参数 schema/结构化协议错误、注册与 active 分离、六个内置工具及统一 `ToolResult` 归一化、模型/持久化输出投影；工具仅通过 `ToolExecutionContext.environment` 传递请求和取消信号。主 Agent 验收时退回修复了未激活 schema 暴露缺口，并独立复验定向测试 14 passed、全量 pytest 44 passed、Ruff lint/format、build、workflow validator、禁止依赖静态扫描与 `git diff --check` 均通过。
- T05 — 已交付 fail-closed `DefaultExecutionPolicy`、交互/非交互 approval port、`ToolExecutor` 单调用生命周期和后端无关最小事件发布协议；覆盖 workspace 相对路径安全、bash ask/auto/yolo/deny、策略/审批异常拒绝、correlation/provider ID 关联、校验/拒绝/普通失败/意外异常归一化及恰好一个 `ToolResult`/事件。主 Agent 验收时退回修复了敏感诊断泄漏和 event ID 注入缺口，并独立复验定向测试 24 passed、全量 pytest 68 passed、对抗脚本、Ruff lint/format、build、workflow validator、依赖边界扫描与 `git diff --check` 均通过。
- T06 — 已交付 Store-first `EventEmitter`、统一 sequence/event ID 管理、append-only UTF-8 JSONL `SessionStore`、逐事件 flush/默认 fsync、有界 payload 头尾预览、安全 JSON 化/递归敏感字段脱敏、同步订阅扇出与逐订阅者结果报告，以及完整记录读取和损坏尾行定位；主 Agent 验收时修复了 usage token 计数被过度脱敏的问题。独立复验定向测试 19 passed、T02/T05 回归 41 passed、全量 pytest 87 passed，并通过凭据/截断/重开/部分失败对抗脚本、Ruff lint/format、build、workflow validator 与 `git diff --check`。
- T07 — 已交付同步 `ModelClient` Protocol 与 OpenAI-compatible Chat Completions 适配器；请求明确传递 messages/active tool schemas/parameters，官方 OpenAI client 默认关闭其内部重试并支持注入 client、httpx transport 或 fake transport。新增无内部 correlation ID 的 `NormalizedAssistantResponse`/`NormalizedToolCall`/`NormalizedUsage`，保留合法 provider tool-call ID、非敏感 JSON arguments 原文和调用顺序、usage 与 finish reason，并将缺失/重复 ID、非法 arguments 和缺失响应归一化为安全诊断。网络/超时、429 与选定状态码使用可注入 sleep/clock 的有界指数退避；认证、权限、模型、配置和 context overflow 分别分类且不误重试；异常、日志和归一化文本/参数按安全边界脱敏，不保留 provider response 或 headers。主 Agent 独立复验 T07 定向测试 25 passed、T02/T05 回归 41 passed、全量 pytest 112 passed，并通过 Ruff lint/format、官方 SDK + `httpx.MockTransport` 的 429/401/context overflow/凭据对抗脚本、build、workflow validator 与 `git diff --check`；未执行真实网关调用。
- T08 — 已交付可独立实例化的同步串行 Agent Loop，完成通用 active Tool 驱动、简单未压缩上下文、交互 follow-up、事件生命周期、预算/协议/墙钟限制、中断/异常关闭、Runtime 隔离与 active-view fail-closed；批次中未执行的声明调用以完整无副作用结果闭合，工具生命周期主 Store 失败不得继续执行。主 Agent 独立复验定向 20 passed、T05/T06 回归 45 passed、全量 134 passed，Ruff lint/format、build、workflow validator、对抗脚本、静态扫描与 diff check 均通过。
- T09 — 已交付显式 system prompt 的保守上下文预算、完整工具交互分组、按 Runtime 隔离的增量压缩、无 tools 总结请求、有界失败退化、covered sequence 事件和 provider overflow 最多一次强制压缩重试；主 Store 失败会回滚投影，原 JSONL 不改写。主 Agent 独立复验 T09 定向 17 passed、T08 回归 20 passed、全量 151 passed，并通过 Ruff lint/format、build、CLI help、workflow validator 与 diff check；未执行真实网关，T12 聚合 acceptance 目录尚未建立。
- T10 — 已交付配置覆盖与副作用前校验、只按环境变量名解析 API key、显式 system prompt/依赖组装、交互/非交互 CLI、approval、Store-first Terminal Renderer、统计、默认 JSONL session 及文档化 stdio/退出码契约。主 Agent 独立复验定向 17 passed、全量 166 passed，并通过 Ruff lint/format、build、workflow validator、CLI help 与 diff check；未执行真实网关，resume、完整 TUI 和 Skill/MCP 具体接入仍排除。
- T14 — 已交付前端与 Agent 后端的命令/事件解耦：CLI 只通过 `AgentBackend` 发送 `SubmitTask`/`ApprovalResponse`/`Interrupt`/`CloseSession` 并按 sequence 游标消费事件；组装迁到 `assembly.py`；`LocalAgentBackend` 在单一 worker 线程中运行既有同步 Loop。交互 bash 确认先落盘 `approval_request` 再等待前端答复；approval 超时/`Interrupt`/`close()`/`request_id` 不匹配 fail-closed；非交互 `ask` 不发请求。T10 退出码/stdio 契约保持不变。主 Agent 独立复验定向 32 passed、T05/T06/T08/T09/T10 回归 107 passed、全量 185 passed，Ruff lint/format、build、CLI help、workflow validator 与 `git diff --check` 均通过。
- T11 — 已交付线性 `--resume`：组装层加载 JSONL、校验并重建 root Runtime（ID、预算计数、active tools、未取消初值、最新 compaction 投影），不重放历史副作用；CLI 只传选项、报告不完整尾行，并从最后 sequence 消费新 follow-up。找不到 session 为退出码 2，损坏/空/缺 root 为退出码 1。主 Agent 独立复验定向 18 passed、T06/T09/T10/T14 回归 77 passed、全量 203 passed，Ruff lint/format、build、CLI help、workflow validator 与 `git diff --check` 均通过。
- T12 — 已建立 AC-01～AC-14 聚合验收套件、扩展静态依赖审查、小型缺陷仓库演练和脱敏 runbook。AC-01 自动化证据是 scripted fake model + LocalEnvironment 六步闭环及非内置 fake Tool 注入。同日用户手动完成真实网关演练（`kimi-k3` / `dashscope.aliyuncs.com`，`2026-08-31T01:16:14Z`）：探索、搜索、修改、`verify.py` 通过、无 tool call 总结；未走到“失败后修正”。主 Agent 独立复验全量 pytest 262 passed、acceptance 50 passed，Ruff lint/format、build、CLI help、workflow validator 与 `git diff --check` 均通过。

## Current State

- 用户已批准 `docs/agent-system-requirements-baseline.md` v1.2 为权威需求正文：首版只保留显式 system prompt 和通用 Tool 两个窄扩展边界，不实现 Skill/MCP 具体功能。
- 开发过程文档已统一迁移到 `docs/baseline/`，并于 2026-08-28 通过用户变更审阅；已完成 T01～T07 的范围和实现证据保持不变。
- 仓库现有可运行的交互/非交互 CLI：通过 `AgentBackend` 发命令、按游标拉事件，并可用 `--resume` 从线性 JSONL 继续 follow-up。至少完成一次 turn 后，进程退出时会在诊断流提示 `coding-agent-neo --resume <session_id>`。
- T01、T02、T03、T04、T05、T06、T07、T08、T09、T10、T14、T11、T12 均已通过各自专用 worker 验证和主 Agent 独立复验。
- 2026-08-30 用户发起前后端解耦变更并完成审阅：架构基线升到 0.3。T14 与 T11 已按该契约落地；需求基线保持 v1.2。
- 2026-08-31 用户明确本基线不是最终提交版本：架构升到 0.4，介绍视频录制从基线实现与 T13 验收中排除；README 与演示脚本仍可先写初稿。
- 标准命令 `python -m pytest tests/acceptance -m acceptance` 现收集并运行 50 项；复现步骤见 `docs/acceptance-runbook.md`。
- 任务顺序仍为 T10 → T14 → T11 → T12 → T13。2026-08-31 用户明确：本基线不是最终提交版本，T13 只写 README 与演示脚本初稿，不录制介绍视频。

## Known Issues

- 已有一次真实网关 AC-01：用户于 2026-08-31 用 `kimi-k3` / `dashscope.aliyuncs.com` 在 `temp/ac01-buggy-counter` 上非交互跑通，结束 `COMPLETED_TURN`（5 steps / 7 tools），`python verify.py` 退出 0。该次第一次编辑即正确，**未**观察到“根据失败结果修正”；该步仍只由 scripted 套件覆盖。未做跨平台 Environment 验证。LocalEnvironment 组件证据来自 macOS Python 3.12.11，shell 仍继承启动用户权限，不是操作系统沙箱。
- 真实演练把 `--session-dir` 放在 workspace 内，`search` 命中正在写入的 JSONL，终端刷出超长行。T13 演示脚本应把 session 目录放到工作区外。
- 尚无且首版不要求 Skill 目录发现/解析/加载或 MCP 客户端/配置/传输证据；T08/T09/T12 的 fake Tool 和显式 prompt 测试只验证核心边界，不代表这两项集成已完成。
- `tests/acceptance` 聚合套件已建立并被 T12 接受：`pytest tests/acceptance -m acceptance` 收集并运行 50 项。真实 API 脱敏记录见 `docs/acceptance-runbook.md`。
- 介绍/演示视频录制、mp4 时长/大小检查和含视频的提交 zip 不在本基线范围；题目原始材料中的视频要求推迟到后续最终版本。T13 只交付 README 与演示脚本初稿，文案可再迭代。公开仓库推送与外部提交仍须用户明确执行或授权，不能由自动化测试单独证明。
- T14 线程相关测试注入了 approval/shutdown/event-poll 超时；生产默认值为 approval 120s、worker 停机 30s、事件轮询 0.1s。未验证跨进程或网络前端。
- 恢复后的首次 follow-up 会再追加一组 `session_start`/`agent_start`（新进程启动，不是重放历史工具）；JSONL 中可能出现多组 start/end。
- README 仍可能写 `--resume` reserved；与实现不一致，计划由 T13 按真实功能改写。
- 题目原始材料的 2026-09-02 24:00 提交时限仍存在，但录制视频与姓名.zip 属于后续最终版本，不是 T13 的通过条件。T13 尚未开始。
- 宿主 Homebrew Python 的直接安装命令受 PEP 668 限制；在 Python 3.12 `.venv` 中执行同一安装命令并完成全部 T01 质量门。

## Next Recommended Task

- T13（准备可审计的 README 与演示脚本初稿）的依赖 T12 已勾选且有验证证据，因此它是下一个应派发的任务。不录制介绍视频，不验收 mp4。外部发布、推送、录屏和最终提交仍须用户明确执行或授权。
