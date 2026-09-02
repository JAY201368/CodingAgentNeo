# Web GUI 历史侧边栏 Agent 工作协议

`requirement.md` 控制增量目标；`ARCHITECTURE.md` 控制技术边界与 Web 内部接口；`TASKS.md` 控制任务范围与依赖；`../agent-transport-interface.md` 是前端接入的唯一权威。除非本文件更严格，否则继承 `../web-frontend/AGENTS.md` 与 `../baseline/AGENTS.md` 的仓库级 Agent 要求。本工作流的产品实现是纯 Web 增量：**T08–T10 只修改 `web/`；T11 可按卡片范围更新本工作流 acceptance、根 README 与静态 acceptance 测试。任何任务都不改 Python 业务源码、`transports/`、`assembly.py`、wire/事件/状态/授权/安全/部署契约或既有 `docs/agent-*.md` 权威规范。**

## 1. 开始工作前

1. 完整阅读本文件、`requirement.md`、`ARCHITECTURE.md`、`TASKS.md`、`PROGRESS.md`、`DECISIONS.md`、当前任务卡及其全部已完成依赖，以及 `../agent-transport-interface.md` 第 4 节（尤其 4.2 history resources、4.5.1 resume、4.7 事件目录）与第 6 节共享规则。不读取 `../agent-backend-interface.md` 或 Python 源码来补充前端契约。
2. 检查 `git status` 与相关文件；保留用户/其他 Agent 的无关改动，范围重叠先报告。
3. 从 `[x]` 和仓库证据确认依赖，不得为未完成依赖发明替代实现。
4. 修改前一句话复述当前任务范围与排除项，一次只处理一个任务 ID。
5. 每个任务必须由全新专用子 Agent 实施；不得领取、开始或继续后续任务。
6. 发现需要改变 wire/事件/状态/授权/游标/安全/部署契约或后端历史/恢复能力时，停止实现，回到 `../backend-history-discover/` 与 `../agent-transport-interface.md` 的跨工作流变更控制。Web 内部接口（`ARCHITECTURE.md` §6.2）细化不属于跨工作流变更，但须遵守本架构边界。

## 2. 标准命令

所有命令沿用 `../web-frontend/AGENTS.md` 已建立的 Web 工程门；本工作流不新增标准命令，也不改动 Python/HTTP/launcher 入口。

| 命令 | 用途 |
| --- | --- |
| `npm --prefix web ci` | 按 lockfile 安装 Web 依赖 |
| `npm --prefix web run dev` | Vite 开发服务（`/api` 代理到本地 Agent HTTP） |
| `npm --prefix web run lint` | Web lint，不改写 |
| `npm --prefix web run type-check` | Vue/TypeScript 类型检查 |
| `npm --prefix web run test` | Web Vitest（可用 `-- <路径>` 聚焦） |
| `npm --prefix web run build` | 生成 `web/dist` |
| `.venv/bin/python -m pytest` | 既有 Python 全量回归（T07/T11 仅核验不回归，不修改 Python 业务源码） |
| `.venv/bin/python -m pytest tests/acceptance -m acceptance` | 既有聚合验收回归（T07/T11） |
| `python /Users/jay/.codex/skills/orchestrate-spec-driven-development/scripts/validate_workflow.py --repo docs/webgui-history-sidebar` | 校验本工作流结构 |

只对当前任务明确路径运行 formatter/自动修复；不得全仓机械重写。前台服务以 Ctrl+C 停止，不编造后台 PID 脚本。若需连真实 Agent HTTP 做人工/端到端检查，先由用户提供未入库配置，且证据须如实标注为真实/离线。

## 3. 模块与依赖边界

- 只在 `web/` 内工作：`web/src/api/client.ts`、`web/src/domain/`（`protocol.ts` 或新增 `history.ts`）、`web/src/composables/`（`useSessionHistory.ts`、扩展 `useAgentSession.ts`）、`web/src/components/HistorySidebar.vue`、`web/src/App.vue`、`web/src/style.css` 及对应测试。
- 只有 `web/src/api/` 发网络请求；组件与 App 不直接 `fetch`/`localStorage`，只经 wire client 与 composable。
- 历史列表/事件/ resume 只走 `docs/agent-transport-interface.md` 第 4 节定义的既有端点；不发明新端点、不拼接路径、不下载原始 JSONL、不接收 workspace/目录/文件名。
- `transport_session_id` 与 canonical `session_id`/history ID/event ID/cursor 严格分离，不混用于请求或持久化。
- 复用既有 reducer/`projectTimeline`/命令互斥；不复制 Agent Loop/Policy/路径安全/持久化逻辑，不改写后端事实。
- localStorage 仍只保存 live transport ID 与最后成功 cursor；不持久化历史列表、历史 session ID、任务正文或 workspace。

## 4. 代码与契约约定

- TypeScript strict；组件 `PascalCase.vue`，composable `useXxx`，变量/函数 `camelCase`，CSS token `--kebab-case`；Vue 使用 Composition API、明确 props/emits。
- 首版不新增 Router、Pinia、UI 组件框架或巨型运行时 schema 依赖；沿用原生 CSS 与既有紫金 token。
- 历史列表 `limit` 1..100（默认 50）、event `since` 0..2^63-1、event `limit` 1..200（默认 200）；list cursor 不透明、≤256 ASCII、原样回送，不解码为 offset/ID/路径；event `next_cursor` 为整数 sequence 用作下次 `since`。
- `session_id`/`resume_session_id` 必须经 `session_...` 安全 token 校验，拒绝 `/`、`\`、`.` 后缀、NUL、控制符、绝对/相对路径与 `.jsonl` 文件名。
- payload/DTO 按不可信 JSON 防御性读取；未知/缺失/截断/坏 diagnostics/超界安全降级，禁止未清洗 `v-html`，不执行 tool/arguments/summary。
- 稳定错误只按 HTTP status 与 `code` 分支，message 不透传后端正文；POST（DELETE/create/command）永不自动重放。
- resume 遵守单活跃规则：先 DELETE 当前再 POST resume；先终结后创建失败时 fail-closed，不残留活跃 session、不自动重建。
- 首次 mount 只允许 history list GET，不得自动 create、attach 或打开 live SSE；localStorage transport hint 只用于用户发起 replacement 时先清理已知 transport。
- 新建与 resume 使用同一个不可重入 lifecycle 锁，并都先 DELETE 已知 transport 再各自执行唯一一次 POST；POST 成功后 transport ID 在获得 DELETE/404/410 终结证据前不得遗忘。
- finite history hydration 只控制历史消息投影；旧 `agent_end`/`session_end`/终止状态不得关闭或覆盖新 resume transport 的 live connection/state/identity。
- session 生命周期入口只在 sidebar：圆形新建按钮与历史选择；右侧主区不得新增结束、重连、新建或 resume 按钮。桌面 sidebar/main 必须独立滚动。
- API Key 只在 Agent 进程；禁止进入 argv 值、HTTP/SSE、浏览器、日志、fixture、snapshot、截图或文档。

## 5. 变更类型与最低验证

| 变更 | 最低验证 |
| --- | --- |
| 流程/架构文档 | 链接、路径、代码块、需求追踪、DAG、与 transport 规范一致性和 skill validator |
| TypeScript API/domain（T01） | success/非法/未知/截断/重复/跳号/网络失败/稳定错误 + 共享 fixture + lint/type/build |
| composable（T02/T03） | 顺序/幂等/跳号/分页/fail-closed/不重放/不混用 ID + lint/type/build |
| Vue 组件/App（T04/T05） | 渲染/选择/加载更多/切换态/成功重现/失败 fail-closed/键盘/aria + lint/type/test/build |
| 视觉（T06） | 组件/build + 桌面/360px/侧边栏折叠/键盘/对比度/reduced-motion 人工检查 |
| 端到端/交付（T07） | scripted Web 验收 + Web 全量门 + 既有 Python 全量/acceptance 不回归 + validator + 运行说明复核 |
| lifecycle 状态核（T08） | create/resume replacement 顺序、共享锁、terminal history 隔离、transport 所有权守恒、再次 replacement + composable/domain test + lint/type/build |
| 侧边栏交互（T09） | mount 零 session 副作用、idle 空白、圆形新建、选择 resume、无右侧 lifecycle 按钮、busy/确认/aria + App/components test + lint/type/build |
| 独立滚动（T10） | Web 全量门 + 1280×800/360×640 真实浏览器 scrollTop/bounding rect/溢出/键盘/对比度/reduced-motion 证据 |
| 变更交付（T11） | T08–T10 聚合旅程 + Web 全量门 + Python acceptance/全量回归 + validator + README/acceptance/secret/生成物复核 |

任务卡更严格时以任务卡为准。未运行、跳过或环境失败必须原样报告，不得写成通过。

## 6. 禁止事项

- T08–T10 不修改 `web/` 以外任何文件；T11 只可额外修改任务卡点名的 `acceptance.md`、README 和静态 acceptance 测试。所有任务均不改 Python 业务源码、`transports/`、`assembly.py`、wire/事件/状态/授权/安全/部署契约或 `docs/agent-*.md`、`docs/backend-history-discover/` 权威规范。
- 不让 Web import Python，不绕过 wire client 或 reducer 改写后端事实，不发明新端点或命令 schema。
- 不接收/持久化 workspace 路径、session 目录、文件名、模型或凭据；不持久化历史列表或 historySessionId；不下载原始 JSONL。
- 不自动重放 POST、不把断线视为关闭/批准/中断、不并发切换构造第二个 backend、不排队第二 turn。
- 不在 mount 时自动 create/attach/SSE，不让 history `session_end` 控制新 live transport，不在 POST 成功后因 hydration/断线/UI 隐藏而丢失 transport ID。
- 不在右侧主区保留或新增 session 结束、重连、新建、resume 按钮，不让 document/body 成为桌面长会话的主滚动容器。
- 不用 `v-html` 渲染未清洗内容、不执行 tool/arguments/summary、不把 `transport_session_id` 与 canonical/history ID 混用。
- 不用 mock 声称真实模型、真实浏览器网络或公网部署通过；不扩大当前卡片、不顺手做下一任务、不降低质量门、不把未运行写成通过。
- 提交 secret、真实 session、任务正文日志、私有路径、本地配置、node_modules、dist、coverage、venv 或大生成物均禁止；Worker 不提交，只有主 Agent 负责验收并提交。

## 7. 交付报告与勾选

Worker 报告必须包含任务 ID/范围、变更模块、可观察行为、实际命令与逐项结果、Acceptance checklist、wire 消费/配置/安全/兼容/下游影响及限制；按事实更新 `PROGRESS.md`，仅在持久非显然选择时追加 `DECISIONS.md`。

主 Agent 独立审阅 diff 与证据，并在实际可行时复跑相称验证。全部验收满足后才勾选并追加日期、行为、边界与真实结果；随后关闭该任务专用 Agent。
