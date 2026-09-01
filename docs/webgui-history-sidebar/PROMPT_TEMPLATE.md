# 子 Agent 派发模板（Web GUI 历史侧边栏）

你在既有仓库 `CodingAgentNeo` 中工作。本工作流是纯 Web 前端增量：**只修改 `web/`，不改 Python 源码、`transports/`、`assembly.py`、wire/事件/状态/授权/安全/部署契约或既有 `docs/agent-*.md` 权威规范。**

## 先阅读（相对 `docs/webgui-history-sidebar/`）

- `AGENTS.md`
- `ARCHITECTURE.md`
- `TASKS.md`
- `PROGRESS.md`
- `DECISIONS.md`
- `../agent-transport-interface.md` 第 4 节（尤其 4.2 history resources、4.5.1 resume、4.7 事件目录）与第 6 节共享规则
- 当前任务依赖的已完成任务卡与相关 `web/` 代码

不要阅读 `../agent-backend-interface.md` 或 Python 源码来补充前端契约。

## 只实现本任务

## Task

[在此粘贴一张完整任务卡：依赖、范围、验收、验证。]

## 要求

- 你是该任务 ID 的唯一专用子 Agent；不得领取或开始任何后续任务。
- 保留无关改动，不实现相邻任务；只修改本任务范围内的 `web/` 文件。
- 编码前先复述任务边界，并从仓库证据确认依赖已完成。
- 遵循 `AGENTS.md` 的标准命令与契约约定：只经 wire client 发请求、防御性解析、稳定错误只按 status/code 分支、POST 不自动重放、单活跃 session、fail-closed、不混用 transport/canonical/history ID、不用 `v-html`、不持久化 historySessionId。
- 运行相称测试/构建（`npm --prefix web run lint/type-check/test/build`，可用 `-- <路径>` 聚焦）。修复范围内失败，如实报告范围外失败。
- 仅在真实、持久且非显然时更新 `PROGRESS.md` 与 `DECISIONS.md`；不勾选任务、不提交（由主 Agent 负责）。

## 完成报告

1. 变更文件/模块
2. 实现的可观察行为
3. 实际运行的命令
4. 测试/构建结果（逐项、真实）
5. Acceptance checklist 对照
6. wire 消费/配置/安全/兼容/下游影响
7. 遗留问题或阻塞
