# CodingAgentNeo Web GUI 历史侧边栏需求

> 状态：执行中（T01–T08 已验收；T09–T11 尚未实施）
> 日期：2026-09-02
> 依赖前序工作流：[../web-frontend/](../web-frontend/)（Web 前端 T01–T10 已交付）、[../backend-history-discover/](../backend-history-discover/)（后端与适配层历史/恢复能力 T01–T06 已交付）
> Agent 适配层规范（前端唯一接入权威）：[../agent-transport-interface.md](../agent-transport-interface.md)

## 用户原始需求

项目已经完成了初版前端的开发（见 `docs/web-frontend/`），随后为后端和传输适配层新增了「提供当前 workspace 下固定专属目录中的 session history」的能力（见 `docs/backend-history-discover/` 和更新后的 `docs/agent-transport-interface.md`）。现在需要为 GUI 前端加上一个侧边栏：

1. 侧边栏显示当前 Workspace 中所有可 resume 的合法 session。
2. 点击某个 session，默认终结当前 session 并 resume 到目标 session，恢复展示目标 session 的所有历史消息。
3. 为了配合该新增侧边栏，原本的元素需要移到侧边栏右侧，改为在侧边栏右侧区域居中。
4. 页面标题可以移到侧边栏顶部。
5. 总体参考 Codex 的设计。

## 2026-09-02 用户反馈与变更要求

现有 T01–T07 实现暴露出以下产品设计和状态机问题；本节是当前增量需求，优先于下方保留的旧完成态描述：

1. 首次打开 GUI 时，不得立即创建或自动接入 session。左侧只加载历史列表和新建入口，右侧主区保持空白，直到用户在侧边栏选择一个可恢复历史 session 或点击新建 session。
2. 左侧边栏与右侧消息区必须是互不影响的滚动容器；滚动右侧历史消息、对话或状态内容时，左侧标题、新建入口和 session 列表的滚动位置不得跟随页面一起移动。
3. 点击历史 session 是一次完整、隐式的 replacement：若前端持有当前 transport 身份，先终结并释放它，再 resume 目标；历史消息重建完成后，新恢复 transport 必须保持可继续 follow-up。历史快照中的旧 `agent_end`/`session_end`/`INTERRUPTED` 只能作为历史展示事实，不能关闭或遗忘刚创建的 live transport，也不能在成功恢复后留下“当前 Session 连接已中断”的伪状态或诱发后续 `409 session_exists`。
4. session 创建、恢复和结束不再由右侧显式生命周期按钮控制。侧边栏上部提供一个小号圆形“新建 session”按钮；点击历史项或该按钮分别隐含执行“结束已知当前 transport → resume 目标”或“结束已知当前 transport → 创建新 session”。右侧移除“结束 Session”“重新连接”“新建 session”等显式 session 生命周期入口；SSE 仍可按既有有限策略自动重连，但不以右侧 session 按钮替代侧边栏选择模型。
5. 活跃 turn/等待授权时的切换确认暂时保留为可逆规划假设：确认后仍执行同一 replacement，取消则不改变当前 session。该确认不是一个独立 session 生命周期入口。

用户已于 2026-09-02 确认本变更计划；按 T08 → T09 → T10 → T11 串行实施。

## 需求澄清与本轮定位

- 本轮是 Web 前端增量，只消费 `docs/agent-transport-interface.md` 第 4 节 HTTP/SSE binding（含 4.2 workspace history resources、4.5.1 resume 创建、4.7 事件目录）与第 6 节共享规则。**不修改** Python 后端、传输适配层、wire 契约或 `docs/backend-history-discover/` 产物。
- 该需求正是 `docs/backend-history-discover/` 与 `docs/agent-transport-interface.md` 中明确「延期的 Web UI 消费」。它 supersede `docs/web-frontend/` 中「首版不提供服务器重启后的历史 resume UI / 不实现历史 session 浏览器」这一排除项；被 supersede 的历史条目仍保留为当时事实。
- 侧边栏数据来自 `GET /api/v1/session-history`（有限 JSON 分页，字段含 `session_id`、`first_user_message`、`created_at`、`updated_at`、`last_sequence`、`last_state`、`resumable`、`diagnostics[]`）。「可 resume 的合法 session」以列表项的 `resumable` 事实为默认展示依据；`resumable` 与 cursor 都不是创建授权，最终以恢复时的后端重验证为准。
- 恢复目标 session 的历史消息来自 `GET /api/v1/session-history/{session_id}/events`（有限 JSON、canonical envelope、按 sequence 升序分页）；resume + live SSE 不会 replay 历史事件，因此历史消息必须由该有限读取端点补齐，再从 resume 返回的 cursor 起接续 live SSE。
- 单活跃 transport session 规则不变：必须先显式 DELETE 当前 transport session，才能 `POST /api/v1/sessions {"resume_session_id":"..."}` 创建恢复 session（否则返回 `409 session_exists`）。因此「终结当前 + resume 目标」在 wire 上是「先关闭再创建」的串行操作。

## 必须交付

1. 一个可 resume session 侧边栏：列出当前 Workspace 历史 session，展示可辨认的摘要（首条用户消息文本、时间、状态、是否可恢复），支持有界分页加载更多，并提供加载中/空/错误状态。
2. 点击某个 session 触发「切换 session」流程：终结当前 transport session、以 `resume_session_id` 创建恢复 session、通过有限历史读取补齐并展示目标 session 的全部历史消息、再从 resume cursor 起接续 live SSE follow-up。
3. 页面整体改为「侧边栏 + 右侧居中主区」布局；页面标题移到侧边栏顶部；原有对话流、composer、授权、消息尾部动态入口等元素迁移到右侧主区并居中。
4. 侧边栏与主区在桌面与窄屏均可用（窄屏可折叠/抽屉化），满足键盘可达、可见焦点、`aria` 语义、对比度与 reduced-motion，延续南京大学紫金浅色视觉，整体参考 Codex 的信息层级。
5. 历史列表读取、历史事件读取和 resume 创建都使用防御性解析：未知/缺失/截断字段安全降级，稳定错误码映射为安全提示，POST 不自动重放。
6. 可复现的运行/验证说明与相称测试；不提交 API Key、真实 session、私有路径或构建产物。
7. 首屏是明确的 idle selection 状态：只读取历史列表，不 `POST /sessions`、不自动 attach 持久化 transport，右侧无“尚未连接/重连/新建”占位卡或按钮。
8. session replacement 使用一个串行、不可重入的生命周期事务；新建与 resume 共用“先释放已知当前 transport，再创建”的边界。`POST` 成功后不得因历史 hydration 的终结事件丢失新 transport 身份。
9. 侧边栏上部的小号圆形按钮是唯一的新建入口；历史项是唯一的 resume/切换入口。主区只负责当前会话内容、composer、授权和安全错误展示，不拥有 session 创建、resume 或结束按钮。
10. 桌面主布局固定在 viewport 内，侧边栏和主区各自纵向滚动；窄屏继续使用可访问 overlay 抽屉，不产生双滚动或横向溢出。

## 范围边界

- 只改 `web/`。不改 Python 源码、`transports/`、`assembly.py`、wire/事件/状态/授权/安全/部署契约或既有 `docs/agent-*.md` 权威规范（如需变更契约先走跨工作流变更控制）。
- 不新增：任意工作区文件浏览器、原始 JSONL 下载、按消息文本搜索、历史删除/重命名/导出、并发/多活跃 session、跨 workspace 控制平面、运行中 steering、消息队列、子 Agent、MCP、Skill、远程/公网部署或认证。
- 浏览器不接收 workspace 路径、session 目录、文件名或凭据；侧边栏只用后端返回的不透明 `session_id` 与有界摘要，恢复只回送不透明 `resume_session_id`。
- 保持单 Agent、单活跃 turn、线性 session 与工具串行语义；resume 不 replay 历史工具副作用。
- T01–T07 是已验收历史基线；2026-09-02 反馈由新增 T08–T11 规划修正。本工作流仍只消费既有历史/恢复 wire 契约，不新增后端能力。

## 验收方向

- 用户能在侧边栏看到当前 Workspace 的可 resume session 列表并翻页；点击一个 session 后，当前 session 被终结、目标 session 被恢复、其历史消息按 turn 正确重现，并可在恢复后的同一 session 继续 follow-up。
- 首次挂载只允许历史列表 GET；在用户选择前 session POST/attach/SSE 均为零，右侧主区为空。
- resume 历史中即使含旧 `session_end(INTERRUPTED)`，恢复成功后也不显示伪“连接已中断”入口；新 transport 身份仍被保留，后续 follow-up 或再次 replacement 不产生由前端遗忘 transport 导致的 `session_exists`。
- 右侧滚动不会改变左侧 `scrollTop`/可见位置；侧边栏圆形新建按钮和历史项覆盖所有 session 生命周期入口，主区不存在显式结束/重连/新建按钮。
- 历史列表/历史事件/resume 创建的稳定错误码（`invalid_history_id`、`invalid_history_cursor`、`invalid_history_limit`、`history_not_found`、`history_unavailable`、`invalid_resume`、`session_exists` 等）映射为安全、可恢复、不误导的提示；坏 candidate 的 diagnostics 不使整表失败；未知/截断 payload 不导致页面崩溃。
- 切换 session 时先终结再创建：resume 失败时不残留活跃 session、不自动重建、给出安全错误与新建入口；resume 成功时历史事件按 sequence 幂等补齐且不重复投影，SSE 从 resume cursor 起接续。
- 侧边栏 + 右侧居中布局在桌面与 360px 窄屏无横向溢出；标题位于侧边栏顶部；键盘、焦点、`aria`、对比度与 reduced-motion 有证据。
- 前端 lint/type-check/test/build 通过；既有 Python 质量门与 baseline/web-frontend/backend-history-discover 回归不被破坏；真实模型/真实浏览器/公网验证与离线 fake 证据明确区分。
