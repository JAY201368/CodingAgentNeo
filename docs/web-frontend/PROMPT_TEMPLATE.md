你正在 CodingAgentNeo 既有仓库中实施一张 Web 前端任务卡。

开始前完整阅读当前任务适用的文档：

- `docs/web-frontend/AGENTS.md`
- `docs/web-frontend/requirement.md`
- `docs/web-frontend/ARCHITECTURE.md`
- `docs/web-frontend/TASKS.md`
- `docs/web-frontend/PROGRESS.md`
- T01/T02/T10：完整阅读 `docs/agent-backend-interface.md` 和 `docs/agent-transport-interface.md`
- T04～T09：只以 `docs/agent-transport-interface.md` 中适用的 HTTP/SSE binding、公开事件目录和共享规则作为接入规范，不读取后端 Port 文档来补充前端契约
- T03：不需要读取 Python 后端或 adapter 接口规范

只实现以下一张任务卡：

## 当前任务

[在派发时替换为一张完整任务卡，必须包含 ID、依赖、范围、验收和验证。]

## 执行要求

- 你是该任务 ID 的全新专用子 Agent，只拥有这一卡；不得领取、开始或声称后续任务。
- 先检查 `git status`、任务依赖的 `[x]` 状态及完成证据，然后用一句话复述范围和排除项再编码。
- 保留用户和其他 Agent 的无关改动；只修改当前范围，不为邻接能力编写临时实现。
- 严格遵守 Port → Backend Service 与 In-process/HTTP 并列 adapter → frontend 的单向依赖边界；HTTP 不得经过 In-process Adapter。若需要改变公开命令、事件、wire、状态、授权、安全或部署契约，停止实现并报告变更控制需求。
- 运行任务卡要求的全部相关检查。只修复范围内失败；范围外或环境失败必须如实报告，不能写成通过。
- 按事实更新 `docs/web-frontend/PROGRESS.md`；只有产生持久且非显然的选择时才追加 `DECISIONS.md`。不要自行勾选任务。

## 完成报告

1. 任务 ID、范围与排除项
2. 变更文件/模块
3. 已实现的可观察行为
4. 实际运行的命令和逐项结果
5. Acceptance checklist
6. Port/adapter/wire/事件/状态/配置/安全/兼容与下游影响
7. 未运行项、限制或阻塞
