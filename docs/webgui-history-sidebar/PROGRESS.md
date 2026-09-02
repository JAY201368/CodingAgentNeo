# CodingAgentNeo Web GUI 历史侧边栏进度

## Completed

- T01 — 历史列表/事件/恢复 wire client 与防御性 DTO：`GET /api/v1/session-history`、`GET /api/v1/session-history/{id}/events`、`POST /api/v1/sessions`（缺省 `{}` 或仅含 `resume_session_id`）按 transport 4.2/4.5.1 消费；非法 ID/cursor/limit 在发请求前失败；稳定码不透传后端正文；共享 `transport-v1.json` 样例。2026-09-01 主 Agent 复跑 Web lint/type-check/test(42 passed)/build/`git diff --check` 通过。
- T02 — resume 旅程与历史 hydration：`resumeSession` 按终结→RESET→创建→hydration→SSE 执行；绑定新 transport 时 hydration 从 cursor 0 起步；创建失败 fail-closed；localStorage 只写新 transport ID/cursor。2026-09-01 主 Agent 复跑 lint/type-check/`test -- src/composables`（30 passed）/build 通过。
- T03 — 历史列表状态与分页 composable：`useSessionHistory` 首次无 cursor 加载、原样回送 `next_cursor` 翻页、refresh 清错误、loading/空/错误三态互斥；失败保留上一页。2026-09-01 主 Agent 复跑 lint/type-check/`test -- src/composables`（42 passed）/build 通过。
- T04 — 展示型历史侧边栏：按序渲染有界摘要，不可恢复项可见但不 emit select，switching 禁用选择，error !== null 即错误态。无 v-html。2026-09-01 主 Agent 复跑 lint/type-check/`test -- src/components`（20 passed）/build 通过。未改 App.vue。
- T05 — sidebar + 右侧居中主区与恢复旅程接线：标题在侧边栏顶部；`select` 接到 `resumeSession`；switching 锁定 composer；失败 fail-closed 并给出新建入口；活跃 turn 一次确认。2026-09-01 主 Agent 复跑 lint/type-check/`test`（120 passed）/build 通过。窄屏抽屉与视觉打磨留 T06。
- T06 — Codex 式侧边栏视觉、响应式与可访问性：640px 起 overlay 抽屉；汉堡 `aria-expanded`/`aria-controls`；Escape/遮罩关闭；状态不只靠颜色。2026-09-02 主 Agent 复跑 lint/type-check/`test`（123 passed）/build 通过。独立浏览器：桌面 1379px 无溢出、无汉堡、侧边栏 288px；360px 无溢出，「历史」打开后 sidebarLeft=0，Escape 关闭。未声称真实 resume 通过。
- T07 — 端到端验收、运行文档与回归门：对照表见 [acceptance.md](acceptance.md)；README 覆盖侧边栏先 DELETE 再 resume；静态扫描无 `v-html`/Python import/误用 fetch。2026-09-02 主 Agent 复跑 Web 124 passed、acceptance 59 passed、四份 workflow validator 通过。全量 pytest 346 passed / 1 failed（本机 ignored 配置未知选项；隔离 `CODING_AGENT_NEO_CONFIG` 后该用例通过）。未跑真实模型/真实浏览器 resume。
- T08 — 隔离历史 hydration 与 live transport 生命周期：`createNewSession()` 与 `resumeSession()` 共用 `lifecycleBusy` 锁；先 DELETE 已知 transport（含未 attach 的持久化 hint）再唯一 POST；历史 envelopes 走 `HYDRATE_EVENT`，旧 `session_end`/`INTERRUPTED` 不关闭或遗忘新 live transport。2026-09-02 主 Agent 复跑 lint/type-check/`test -- src/composables src/domain`（83 passed）/build/`git diff --check` 通过。未改 App/侧边栏/CSS。
- T09 — 侧边栏驱动的 idle / 新建 / resume：删除 App `autoConnect`；首屏只拉历史列表，右侧空白。圆形「新建 session」接到 `createNewSession()`，历史项接到 `resumeSession()`；主区无结束/重连/新建按钮。2026-09-02 主 Agent 复跑 lint/type-check/`test -- src/App.spec.ts src/components`（50 passed）/build 通过。独立浏览器：idle 主区空白、侧栏圆形 +、无主区 lifecycle 按钮。
- T10 — 固定 viewport shell 与左右独立滚动：document/body 不再主滚动；sidebar 与右侧消息区各自 overflow；composer 为右侧 in-flow 底栏。2026-09-02 主 Agent 复跑 lint/type-check/`test`（149 passed）/build 通过。独立浏览器 1280×800 左右独立滚动无横向溢出；360×640 抽屉自身可滚、Escape 焦点返回。
- T11 — 聚合回归四个纠偏旅程并同步交付文档：`acceptance.md` 映射 T08–T10 八条旅程到真实 Vitest；README 改为打开 GUI 只列历史、侧边栏新建/选择、先 DELETE 已知 transport、history terminal 只属历史投影、左右独立滚动。2026-09-02 主 Agent 复跑 Web 149 passed、acceptance 59 passed、全量 pytest 347 passed、workflow validator 11 tasks OK。未跑真实模型/真实浏览器 resume。

## Current State

- T01–T11 全部验收。0.2 纠偏已落地并可运行：
  - 打开 GUI 只加载历史列表，右侧空白，不自动 create/attach。
  - 侧边栏圆形新建与历史选择共用 replacement：先 DELETE 已知 transport，再唯一 POST。
  - 历史 `session_end`/`INTERRUPTED` 只重建 timeline，不关闭新 live transport。
  - 桌面左右独立滚动；主区无结束/重连/新建按钮。
  - 对照见 [acceptance.md](acceptance.md)。README 与静态扫描描述同一完成态。
  - 主 Agent 复跑（2026-09-02）：Web lint/type-check/`test` **149 passed**/build；Python acceptance **59 passed**；全量 pytest **347 passed**；workflow validator 11 tasks OK。未声称真实模型网关、真实浏览器 resume 或公网通过。
- T10 已验收。可观察行为：
  - `html, body, #app` 固定 viewport（`height: 100%` / `max-height: 100dvh`，`overflow: hidden`）。document/body 不再是长会话主滚动。
  - 桌面 sidebar 与 `.conversation-workspace__scroll` 各自 `overflow: auto`；滚右不带动 sidebar `scrollTop`/rect，滚左不带动右侧 `scrollTop`。
  - composer 属于右侧 `.conversation-workspace` 底部栏，`position: relative`。
  - 圆形新建按钮 2.25rem 紫底金边；disabled 为 opacity + dashed border。
  - 窄屏 ≤640px overlay 抽屉、inert、Escape/遮罩保持。
  - 主 Agent 复跑（2026-09-02）：lint / type-check / `test` **149 passed** / build / `git diff --check` 通过。
  - 独立浏览器（Cursor Chromium，`http://localhost:5173/`）：
    - **1280×800**：`iw=ih` 1280×800；`scrollWidth===clientWidth===1280`；html/body overflow hidden。滚右：sidebar `{x:0,y:0,w:288,h:800}` 且 `scrollTop=0` 不变，pane `scrollTop=800`。滚左：sidebar `scrollTop=500`，pane 仍 800。两端可到底。新建按钮 36px / `border-radius:50%`。
    - **360×640** 关闭：无横向溢出；汉堡「历史」；sidebar `x=-288` / inert / hidden。打开：`x=0` 自身可滚（`scrollTop=400`、document 0），shell inert，标题在汉堡下方。Escape 关闭且焦点回「打开历史」。
- T09 已验收。首屏 idle、侧边栏新建/resume、主区无 lifecycle 按钮。
- T08 已验收。hydration/live 隔离与统一 replacement 由 composable/reducer 提供。
- T07 已验收。scripted 对照见当时 [acceptance.md](acceptance.md)；T11 已把该表更新为 T09 改名后的真实用例，并追加 T08–T10 纠偏旅程。
- T06 已验收。可观察行为：
  - 桌面（≥641px）：侧边栏占文档流 18rem，汉堡不渲染；主区在侧边栏右侧居中；composer `left: var(--sidebar-width)`。
  - 窄屏（≤640px）：侧边栏默认移出文档流（overlay）；「历史」按钮 `aria-controls="history-sidebar"`；打开后滑入（reduced-motion 下近乎瞬间）、半透明遮罩点击关闭、Escape 关闭并焦点回到按钮；主区 `.app-shell` `inert`。选择 session 后关闭抽屉。状态不写 localStorage。
  - 信息层级：摘要为主、元信息次之；当前项左边框 +「当前」文字 + 加粗；不可恢复虚线左边框 +「不可恢复」深金文字；切换有「正在切换 session…」。
  - 人工视觉检查（Cursor 内置浏览器，Chromium；Agent HTTP 可能在线但**不声称真实 resume 通过**）：
    - 1280×800：无横向溢出（`scrollWidth===clientWidth===1280`）；无汉堡；侧边栏 288px in-flow；主区从 x=288 起、shell 宽 832 居中；标题南大紫。
    - 360×640 关闭：无横向溢出；汉堡「历史」可见；主区宽 360；composer `left:0`、宽 336；侧边栏 `translateX(-288)` / `visibility:hidden` / `inert`。
    - 360×640 打开：侧边栏 x=0 可见；遮罩存在；标题在汉堡下方不被挡住（开态 `padding-top`）；Escape 关闭且焦点回汉堡。
    - 对比度（计算）：正文 14.83、标题紫/白 11.83、muted 7.88–8.57、subtle 6.39、gold-ink 7.65，均 ≥ WCAG 2.2 AA。
    - reduced-motion：抽屉 `transition-duration` 为 `0.01ms`（`none 1e-05s`）。
    - 发现与修正：开态汉堡盖住标题 → 开态侧边栏增加 `padding-top`；窄屏 Tab 可能落到遮罩后的 composer → 开态 `.app-shell` `inert`。
  - Worker 命令：`npm --prefix web run lint` 通过；`type-check` 通过；`test` 123 passed；`build` 通过。

- 2026-09-01 已完成工作流初始化：`requirement.md`、`ARCHITECTURE.md`、`TASKS.md`、`AGENTS.md`、`PROMPT_TEMPLATE.md`、`PROGRESS.md`、`DECISIONS.md` 就位，`validate_workflow.py` 结构校验通过。
- 依赖前序工作流均已交付：Web 前端 `../web-frontend/`（T01–T10）与后端/适配层历史/恢复能力 `../backend-history-discover/`（T01–T06）。
- T01 已验收（见 Completed）。
- T02 已验收。可观察行为：
  - `useAgentSession().resumeSession(historySessionId)` 按「校验 canonical ID → 拒绝 switching 重入 → `stopEvents` → `DELETE` 当前 transport（无则跳过；404/410 视为已关闭）→ `RESET` → `POST /sessions {resume_session_id}` → 绑定新 transport 且 hydration 从 cursor 0 开始 → 分页 `readSessionHistoryEvents(since=0)` 经 `EVENT` 注入 → 从 reducer 当前 cursor `startEvents()`」执行。
  - 暴露 `switching`；切换中拒绝第二次 `resumeSession`。不调用 composable `deleteSession()`（避免 `connected` 门槛与 `CLOSED`）。
  - 绑定新 transport 时 **不用** resume 响应 cursor 作为 hydration 起点；历史事件从 `since=0` 注入，由 reducer 推进 cursor；live SSE 使用 hydration 后的 reducer cursor（`sequence > cursor`）。
  - 先终结后创建失败（404/422/409 稳定码或创建阶段网络失败）进入 `SESSION_UNAVAILABLE`：无活跃 transport、不自动新建、不重放 POST；错误用 T01 客户端安全短句。DELETE 网络失败则停止、不 POST resume。
  - localStorage 只写新 transport ID/cursor，不写 historySessionId。恢复后 `finalAssistantText` / `projectTimeline` 可重现历史消息，`COMPLETED_TURN` 后可 `submitTask` follow-up。
  - 未实现 App 布局或视觉（T05/T06）。`HistorySidebar` 已由 T04 交付。
  - Worker 验证：`npm --prefix web run lint`、`type-check`、`test -- src/composables`（30 passed）、`build` 均通过。
- T03 已验收。可观察行为：
  - `useSessionHistory({ client?, autoLoad? })` 管理历史列表：`items`、`loading`、`error`、`hasMore`、`refresh()`、`loadMore()`。默认 `autoLoad=true` 在 composable 创建时调用 `listSessionHistory({ signal })`，不传 cursor、不传默认 limit。
  - `loadMore()` 把上次 `next_cursor` **原样**作为 `cursor` 回送并按 `session_id` 去重追加；`hasMore === (next_cursor !== null)`；无 cursor 或已有 in-flight 请求时 no-op。
  - `refresh()` 立即清 error、中止过期 loadMore（generation + AbortController），无 cursor 重载首页。
  - 三态互斥：`loading` 仅覆盖首次/刷新；空 = 非 loading 且 `items.length===0` 且 `error===null`；错误为 `{code, message}`（T01 客户端短句），失败时保留上一页 items，不假装完整成功列表。
  - 不调用 `readSessionHistoryEvents`/`createSession`/`resumeSession`，不 import `useAgentSession`，不写 localStorage，不与 live 命令互斥。
  - 主 Agent 复跑（2026-09-01）：`npm --prefix web run lint` 通过；`type-check` 通过；`test -- src/composables` 42 passed（含既有 T02 30 + T03 12）；`build` 通过。

## Known Issues

- 活跃 turn/等待授权时保留一次确认；确认后执行 replacement，取消则无副作用。这是已落地的可逆假设，不是未完成缺陷。
- 「切换 session」在 wire 上是「先 DELETE 当前、再 POST resume」的串行操作（单活跃 session 规则）；先终结后 resume 失败会导致当前 session 已终结且无活跃 session，须 fail-closed 提示新建，不自动重建（见 `ARCHITECTURE.md` §3.3、`DECISIONS.md`）。T02 状态核与 T05 App 接线均已覆盖该窗口：锁定侧边栏/composer、安全错误、新建入口。
- resume + live SSE 不 replay 历史事件；目标 session 历史消息由有限历史读取 hydration 后再接续 SSE。T02 覆盖幂等/跳号/多页/截断/未知事件；T05 已把 hydration 结果接到主区 timeline。
- 活跃 turn/等待授权时切换的一次 `window.confirm` 仍为可逆假设（默认仍终结当前）；若用户希望「运行中也直接无提示切换」或「运行中禁止切换」，须更新 `requirement.md`、`ARCHITECTURE.md` 与 DECISIONS。
- 视觉「参考 Codex 设计」为方向性描述；T06 已落地 640px overlay 抽屉、紫金信息层级与人工 1280/360 证据。南大金色仍为产品可访问 token，非校方官方 HEX。
- 真实模型网关、真实浏览器端到端 resume 与公网部署不在本轮执行；相关证据只能标注为离线/脚本化，不得冒充真实验证。T06 的 1280/360 布局检查不替代 resume 状态机证据。
- 浏览器 `since` 只能精确表示 `Number.MAX_SAFE_INTEGER`（2^53-1）以内的整数，是 wire `0..2^63-1` 的 JS 安全子集；超出安全整数的值在发请求前按 `invalid_history_cursor` 拒绝。
- 本机被 gitignore 的 `.coding-agent-neo.toml` **可能**仍含已移除的 `session_dir`，会使 `tests/integration/test_resume_cli.py::test_main_corrupt_session_is_startup_failure` 在未隔离 `CODING_AGENT_NEO_CONFIG` 时读到 unknown option 并返回 EXIT_CONFIG(2)。T11 本次全量 pytest **未复现**（本机该文件当前无 `session_dir`，347 passed）。不在本工作流修复范围，未改 Python。

## Next Recommended Task

- 无未实施任务。0.2 纠偏里程碑已验收。
