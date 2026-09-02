# CodingAgentNeo Web GUI 历史侧边栏决策日志

本文件按时间追加持久、非显然且影响下游的选择。Bootstrap 条目保留当时规划语境；当前实施与验收完成态以 `TASKS.md` 和 `PROGRESS.md` 为准。

## 2026-09-01 — Bootstrap 定位：落地被延期的 Web UI，纯 Web 增量

- 选择：新建 `docs/webgui-history-sidebar/` 工作流，只在 `web/` 内消费既有历史/恢复 wire 契约，不改 Python、`transports/`、`assembly.py` 或任何 `docs/agent-*.md`/`docs/backend-history-discover/` 权威规范。
- 理由与替代：后端与适配层的历史发现/恢复能力已由 `../backend-history-discover/` 交付，且 `../agent-transport-interface.md` 明确「Web UI 消费延期」。把该消费实现放到独立 Web 工作流，可保持后端契约冻结、边界清晰；若混入后端工作流会模糊「后端能力 vs 前端消费」的所有权。
- 后果：本工作流 supersede `../web-frontend/` 中「首版不提供历史 resume UI / 不实现历史 session 浏览器」的排除项（历史条目保留为当时事实）；任何需要后端新增能力的诉求都必须回到跨工作流变更控制，而不是在本工作流内改 Python。

## 2026-09-01 — 切换 session 为「先终结当前、再创建恢复」串行操作

- 选择：点击侧边栏 session 时，先 `DELETE /api/v1/sessions/{transport_id}` 终结当前 transport session，再 `POST /api/v1/sessions {"resume_session_id"}` 创建恢复 session；两步串行，不并发。
- 理由与替代：wire 规范的单活跃 session 规则要求存在活跃 session 时创建返回 `409 session_exists`，因此无法「先创建后切换」。这与需求「默认终结当前 session 并 resume 到目标 session」一致。
- 后果：存在「已终结当前但 resume 创建失败」的窗口。此时没有活跃 session，UI 必须 fail-closed 显示安全错误并提供新建入口，不自动重建、不自动重放。`resumable`/cursor 不是授权，恢复以后端重验证为准。

## 2026-09-01 — 历史消息由有限历史读取 hydration，再接续 live SSE

- 选择：resume 成功后，从 `since=0` 分页调用 `GET /api/v1/session-history/{session_id}/events`，把 canonical envelope 按 sequence 升序逐条经既有 reducer 的 `EVENT` action 注入以重现历史消息；hydration 到 `has_more=false` 后，从 reducer 当前 cursor 起 `startEvents()` 接续 live SSE。
- 理由与替代：resume + live SSE 不 replay 历史事件（wire 规范 4.5.1），只订阅 SSE 无法显示历史消息。历史读取是有限 JSON 快照，复用既有 reducer/`projectTimeline` 即可无第二套投影逻辑地重现消息，并天然获得重复幂等、跳号降级、未知/截断安全降级。
- 后果：切换前必须 `RESET` 清空旧投影；hydration 使用目标 canonical `session_id`（与 `transport_session_id` 分离）。hydration 与后续 SSE 的 cursor 必须衔接，避免重复或跳过；历史 read 的并发追加只影响 `has_more`/摘要，不破坏 cursor 语义。

## 2026-09-01 — 布局：侧边栏 + 右侧居中主区，标题移入侧边栏（参考 Codex）

- 选择：`App.vue` 改为「左侧固定侧边栏 + 右侧居中主区」；页面标题移到侧边栏顶部，session 列表在其下；原有整页对话流、固定底部 composer、授权与消息尾部动态入口迁入右侧主区并水平居中。窄屏侧边栏折叠/抽屉化。
- 理由与替代：需求明确要求侧边栏、右侧居中与标题移入侧边栏，并「总体参考 Codex 设计」。保留既有对话流与 composer 语义可最小化对已验收 Web 旅程的破坏；不引入 Router/Pinia/UI 框架以守住轻量前端边界。
- 后果：既有整页滚动、固定 composer、按 turn 折叠等展示契约在主区内保持；侧边栏是新增视图层，不改变命令/事件/授权/安全契约。Codex 的具体视觉细节为方向参考，T06 落地并留人工证据，不复制其品牌资产。

## 2026-09-01 —（可逆规划假设）活跃 turn 时切换给出一次显式确认

- 选择：默认行为是点击即终结当前并 resume；但当前 session 存在活跃 turn 或等待授权时，先弹出一次显式确认再终结，确认取消则不改变当前 session。
- 理由与替代：需求说「默认终结当前 session」，未明确活跃 turn 中途切换的期望。直接无提示终结可能让用户误触丢失正在运行的工作；一次确认在不违背「默认终结」的前提下提供 fail-safe。替代方案是完全无确认（更贴近字面「默认」）或运行中禁止切换（过度限制）。
- 后果：这是可逆规划假设，标注在此并在 `PROGRESS.md`/`TASKS.md` T05 体现。若用户希望「运行中也直接无提示切换」或「运行中禁止切换」，须在实现 T05 前更新 `requirement.md`、`ARCHITECTURE.md` 与本条决策；在确认前 T05 仍可实现，但确认交互须易于调整。

## 2026-09-01 — T01：`createSession` 第一参数探测 AbortSignal，保持既有调用点

- 选择：扩展 `createSession(resumeSessionId?, signal?)` 时，若第一参数是 `AbortSignal` 则仍发送空 `{}` 并把它当作 abort signal；只有非 signal 的字符串才作为 `resume_session_id`。
- 理由与替代：既有 `useAgentSession` 以 `createSession(signal)` 调用。把 AbortSignal 当成 resume ID 会发非法 body。替代方案是改调用点为 `createSession(undefined, signal)`，同样属于 T01 兼容修复，但探测第一参数可避免改 composable（T02 范围）。
- 后果：T02 新增 `resumeSession` 时应显式传 canonical `resumeSessionId`（或 `createSession(id, signal)`），不要依赖位置参数的类型猜测以外的语义。

## 2026-09-01 — T01：历史 `since` 在浏览器按 JS 安全整数校验

- 选择：`readSessionHistoryEvents` 的 `since` 在发请求前要求 `Number.isSafeInteger` 且 `>= 0`（上限 `Number.MAX_SAFE_INTEGER`），而不是尝试表示 wire 的完整 `0..2^63-1`。
- 理由与替代：JavaScript `number` 无法精确表示 2^63-1。把超安全整数的值编码进 query 会静默丢失精度。拒绝它们并映射 `invalid_history_cursor` 比发送错误 cursor 更安全。
- 后果：超过 2^53-1 的 sequence 无法作为浏览器 `since` 发出；当前事件 sequence 从 1 递增，实际不会触及该上限。若未来需要全范围整数，须改用字符串十进制 query 并作为跨工作流契约变更。

## 2026-09-01 — T02：非法 history ID 在终结当前 session 之前失败

- 选择：`resumeSession` 先用 T01 的 `session_...` 校验拒绝非法/混用 ID（如 `../x`、transport ID、`.jsonl`），再进入 switching、DELETE 与 POST。
- 理由与替代：任务卡顺序把 DELETE 写在 create 之前，但非法 ID 在发 POST/GET history 前就必须失败。若先 DELETE 再因非法 ID 失败，会无意义地终结当前活跃 session。替代方案是严格按字面先 DELETE，fail-closed 更重、对误点代价更大。
- 后果：非法 ID 不改变当前 transport session，也不发任何 history/resume 请求。合法 ID 仍先 DELETE 再 POST，以遵守单活跃规则。

## 2026-09-01 — T02：hydration 把 domain envelope 再编码为 wire 后走既有 EVENT

- 选择：绑定新 transport 时 `CONNECTED` 的 cursor 固定为 `0`，resume 响应 cursor 只作 hydration 完成后的对照；`readSessionHistoryEvents` 返回的 camelCase domain envelope 在 dispatch 前再编码为 `schema_version`/`session_id` wire 对象。
- 理由与替代：resume 响应 cursor 是恢复文件最后 sequence。若把它当作 reducer 起点，历史事件会被当成重复而全部忽略。T01 的 history parser 产出 domain envelope，而 reducer 的 `EVENT` 路径与 live SSE 一样只吃未信任 wire JSON。在 composable 边界再编码，可复用同一套幂等/跳号/未知/截断降级，避免第二套投影。替代方案是让 reducer 同时接受 camelCase，或让 T01 暴露原始 JSON——前者扩大 reducer 契约，后者回退 T01。
- 后果：hydration 结束后 reducer cursor 收敛到恢复文件最后成功消费的 sequence；`startEvents()` 必须从该 cursor 订阅（`sequence > cursor`）。T05 接线时不要把 resume cursor 写入 reducer，也不要把 historySessionId 写入 localStorage。

## 2026-09-01 — T03：列表错误保留上一页，`loading` 只覆盖首页/刷新

- 选择：`useSessionHistory` 的 `error` 为 `{code, message}`，message 只取 T01 `AgentApiError`/`AgentNetworkError` 的客户端短句；`loading` 仅在首次加载与 `refresh()` 期间为 true，`loadMore` 用内部 in-flight 闸门防重入。刷新/追加失败时保留上一页成功 `items` 并单独标 error。默认 `autoLoad` 在 composable 创建时立即请求（与 `useAgentSession` 一致），请求只带 `AbortSignal`，不传默认 `limit`/`cursor`。
- 理由与替代：架构允许「保留上一页并单独标 error」，这样错误与「纯空成功」可区分，也不会在翻页失败时把已渲染列表清空。把 `loading` 限于替换型加载，避免 T04 把「加载更多」误显示成整表 spinner。替代方案是失败即清空 items，或把 loadMore 也算进 `loading`，都会让三态或分页控件更难用。
- 后果：T04 应以 `error !== null` 作为错误态，不要用 `items.length===0` 兼做错误；「加载更多」进行中 `loading` 仍为 false，重复点击由 composable no-op。列表不持久化、不与 live session 命令互斥。

## 2026-09-01 — T04：侧边栏只插值摘要，不可恢复项不 emit select

- 选择：`HistorySidebar` 用 `safeDisplayText` + `{{ }}` 渲染 `first_user_message.text`，不把 `BoundedText` 放进选择按钮；`resumable===false` 的项保持可见并带「不可恢复」文字标记，点击不 emit `select`。错误态以 `error !== null` 为准，即使 `items` 非空也显示安全提示与「重试」。
- 理由与替代：`BoundedText` 自带展开按钮，放进选择 `<button>` 会嵌套交互控件。不可恢复仍展示可避免用户以为列表丢了条目，但不 emit 可避免 T05 对不可恢复 ID 发起 resume。替代方案是复用 `BoundedText` 或对不可恢复项仍 emit 由父级拒绝，前者破坏语义，后者把防护推到接线层。
- 后果：长摘要不在侧边栏内展开（T06 可用 CSS 截断）；T05 接线仍应只对 resumable 选择调用 `resumeSession`。诊断只展示 `code`，不渲染 `message`（避免路径泄漏）。最终 Codex 视觉与窄屏抽屉仍属 T06。

## 2026-09-01 — T05：活跃 turn 确认用 `window.confirm`，标题走侧边栏 slot

- 选择：T05 用 `window.confirm('将终结当前正在进行的工作并切换 session')` 落地可逆假设；`HistorySidebar` 增加可选 `title` slot，App 把 `h1#app-title` / eyebrow 投影进侧边栏，组件仍只渲染/emit。`session_exists` 在 resume 失败语境映射为「当前 session 已结束，请新建」，避免沿用创建态的「请关闭其他页面」。
- 理由与替代：`window.confirm` 无额外 UI 框架、测试可 spy，后续可换成应用内对话框而不改 resume 顺序。title slot 避免 App 再包一层非语义 header，也不让侧边栏自己拥有文案/fetch。`session_exists` 在先 DELETE 后出现几乎总是切换窗口而非「别的页面还开着」。
- 后果：确认取消不发 DELETE/resume；成功后只把 canonical history id 记为 `activeSessionId` 并刷新列表。窄屏抽屉、对比度与 Codex 信息层级仍留 T06。若产品改为「运行中禁止切换」或「无提示直接切换」，只需改 App 确认分支。

## 2026-09-02 — T06：窄屏抽屉用 640px 断点、内存态 overlay，不持久化

- 选择：沿用既有 `@media (max-width: 640px)` 作为抽屉断点（不另开 720px）；窄屏侧边栏 `position: fixed` 滑出文档流，主区全宽，composer `left: 0`。打开/关闭只存在 App 内存（`matchMedia` + `historyDrawerOpen`），不写 localStorage。桌面不渲染汉堡按钮；窄屏打开时主区 `.app-shell` 设 `inert`，避免 Tab 落到遮罩后的控件。
- 理由与替代：任务允许 640 或 720，扩展已有 640 规则可保留既有窄屏与 `prefers-reduced-motion` 策略。若侧边栏在窄屏仍占 18rem 文档流，固定 composer 会横向溢出。替代方案是 CSS-only `:target` 抽屉（键盘/Escape/测试更难）或把开关写入 localStorage（违反本工作流「列表/UI 态不持久化」）。
- 后果：jsdom 测试通过 stub `matchMedia('(max-width: 640px)')` 断言汉堡出现与 Escape/遮罩关闭。真实浏览器 360px 无横向溢出；reduced-motion 下抽屉 transition 被既有全局规则压到约 0.01ms。T07 只核验本卡布局，不把离线视觉检查写成真实 resume。

## 2026-09-02 — T07：scripted 验收以既有 Vitest 为权威，不新增 Python live Web UI 旅程

- 选择：T07 把列出/分页/切换/fail-closed/hydration/follow-up/降级/ID 分离映射到既有 App/composable/client/history Vitest；只补 App 级「加载更多」接线。Python `tests/acceptance/test_webgui_history_sidebar_acceptance.py` 只扫描静态边界与 README，不启动浏览器、不调用真实模型。README 删除「there is no … server-restart Web resume」，改为描述侧边栏有限 JSON + `resume_session_id` 恢复路径。
- 理由与替代：resume hydration 是浏览器 reducer 状态机，fake fetch 已覆盖顺序与 fail-closed；Python HTTP 历史/resume 已由 `docs/backend-history-discover/` 与 `tests/integration/test_http_history.py` 证明。再写一套 live HTTP「假装点侧边栏」的 Python 测试会复制 wire 而不增加 UI 证据。替代方案是 Playwright 真浏览器，本轮用户未提供未入库环境。
- 后果：T07 完成态以 [acceptance.md](acceptance.md) 对照表 + Web 全量门 + 既有 Python 回归为准。未运行的真实模型/真实浏览器必须保持未声称。

## 2026-09-02 — 0.2 变更：首屏改为无 session 副作用的选择态

- 选择：首次挂载只读取历史列表，右侧主区保持空白；不自动创建新 session，也不因 localStorage transport hint 自动 attach。只有侧边栏历史项和上部圆形新建按钮能启动 session replacement。
- 理由与替代：现有 `autoConnect → connect()` 把“打开 GUI”误当成“创建 session”，会先占用 single-active registry，再迫使历史切换额外清理。保留自动 attach 虽能延续刷新重连，但与用户明确要求“先在左侧选择 resume 或新建”冲突。
- 后果：localStorage transport hint 降级为 replacement 前清理已知 transport 的线索，不再是 mount 自动连接授权。右侧显式结束/重连/新建入口由 T09 删除；SSE GET 仍可在已选择 session 内有限自动重连。此条 supersede T05/T07 完成态中由右侧 session entry 卡承担失败恢复入口的设计，但保留其历史记录。

## 2026-09-02 — 0.2 变更：历史投影不能控制新 resume transport 生命周期

- 选择：finite history hydration 只重建 timeline/历史 cursor；历史中的旧 `agent_end`、`session_end` 和终止状态不得关闭、遗忘或覆盖 resume POST 刚创建的 live transport。live connection/state/identity 只受创建/attach 结果与随后 live SSE 控制。
- 理由与替代：现实现把 history envelope 重新编码后直接 dispatch 到 live `EVENT` reducer。目标历史若以 `session_end(INTERRUPTED)` 结束，消息虽能还原，但 reducer 会把新 transport 标为 closed 并清除 ID；backend registry 仍持有它，随后空 body create 命中 `409 session_exists`。仅改错误文案或再点一次重连不能修复所有权丢失。
- 后果：此条 supersede 2026-09-01 “hydration 把 domain envelope 再编码为 wire 后走既有 EVENT”中“历史事件可以无条件驱动完整 live reducer 状态”的部分；复用 parser/timeline、重复/跳号/截断降级仍保留。T08 必须引入带来源语义的 action、独立历史投影或等价隔离，并证明 POST 成功后的 transport ID 所有权守恒。

## 2026-09-02 — 0.2 变更：新建与历史选择是统一 replacement，左右独立滚动

- 选择：新建和 resume 共用串行 lifecycle 锁，均先 DELETE 前端已知当前 transport，再执行各自唯一 POST；session 控制入口只存在于侧边栏。桌面根布局固定于 viewport，sidebar 与主区分别纵向滚动。
- 理由与替代：仅让 resume 先 DELETE 而新建直接 POST 会在持久化 hint 或失败残留下重复出现 409；继续保留右侧生命周期按钮会让状态转换入口分散。document/body 主滚动又会让侧栏随右栏长消息移动，违背导航常驻预期。
- 后果：T08 先稳定统一 replacement，T09 再改入口，T10 最后改滚动/视觉，T11 聚合验收。活跃 turn 的一次确认暂时保留为可逆假设；用户确认计划前不实施、不派发 subagent。

## 2026-09-02 — T08：历史 hydration 走 `HYDRATE_EVENT`，live 生命周期仍只认 `EVENT`

- 选择：reducer 新增 `HYDRATE_EVENT`。它复用同一套 envelope 解析、timeline 追加、sequence 幂等/跳号诊断；但保留 live `connection` / `transportSessionId` / `streamAvailable`，并且若历史事件把 `status` 推进到 `INTERRUPTED`/`FAILED`/`LIMIT_REACHED`，则恢复 hydration 前的 live status。`useAgentSession.dispatch` 只在 live `EVENT` 的 `session_end` 关闭时清除持久化 hint。`createNewSession()` 与 `resumeSession()` 共用 `lifecycleBusy` 锁；`switching` 为其布尔派生。已知 transport 取 `state.transportSessionId ?? storedHint.transportSessionId`。既有 `connect()` 保留给 App 挂载，直到 T09。
- 理由与替代：把历史 envelopes 再编码后走 live `EVENT` 会让旧 `session_end` 关闭刚 POST 出的 transport 并清掉 ID。独立第二套 timeline 会复制解析与降级规则。仅在 composable 里忽略 `session_end` 仍会让 `agent_end`/`INTERRUPTED` 把 command gate 打进终止态，follow-up 失败。带来源的 reducer action 是最小隔离。
- 后果：此条落实 2026-09-02 “历史投影不能控制新 resume transport 生命周期”。hydration 失败或 SSE 断线不再是遗忘 transport ID 的证据。T09 可把侧边栏新建/选择接到 `createNewSession`/`resumeSession`，但不得再让历史 `EVENT` 驱动 live 关闭。

## 2026-09-02 — T09：去掉 App `autoConnect`，恢复入口只留侧边栏

- 选择：删除 App 的 `autoConnect` prop 与 mount `connect()` 路径，而不是保留 `autoConnect: false` 作为测试开关。活跃 turn 的 create 与 resume 共用同一句 `window.confirm`。fail-closed/关闭类安全文案改为“请从左侧侧边栏新建或选择历史 session”，主区不再渲染新建/重连/结束按钮。
- 理由与替代：保留 `autoConnect: true` 会让旧测试继续走自动创建，与“首屏零 session 副作用”冲突。create 与 resume 分两套确认文案没有产品差异；主区保留按钮作 fallback 会再次分散生命周期入口。
- 后果：composable `connect()` 仍供内部 SSE 有限重试使用，App 不再调用。localStorage transport hint 只在用户点击新建/历史项时由 T08 replacement 清理。独立滚动与圆形按钮最终紫金几何仍属 T10。
