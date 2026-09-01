# T08 视觉与可访问性检查记录

日期：2026-09-01

本记录只覆盖 T08：既有单 Agent session 旅程的浅色紫金视觉、窄屏布局、状态可辨识度、键盘与 reduced-motion 路径。没有新增业务、事件/wire 字段、路由、部署入口、校徽或 UI 组件库。

> 历史证据说明：下列 IAB 矩阵记录的是 T08 验收当时的界面，其中仍有独立工具卡、Stop 和状态展示。后续 UI 已收敛为整页消息流、固定底部箭头 composer、按 turn 折叠思考过程及消息尾部动态入口；因此本表只证明当时版本，不作为当前布局的真实浏览器视觉验证。当前设计以 [ARCHITECTURE.md](ARCHITECTURE.md#35-当前-web-消息流与控制布局) 为准。

## 人工浏览器矩阵

检查表面：Codex In-app Browser（IAB），本地 Vite 页面通过 `http://127.0.0.1:5173/` 访问；完整旅程使用本地 scripted HTTP 演示服务，仅生成脱敏演示事件，不代表真实模型或公网部署。

| 检查 | 尺寸/路径 | 结果 | 发现与修正 |
| --- | --- | --- | --- |
| 桌面空态/错误态 | 1280×720 viewport | 通过；`document.documentElement.scrollWidth` 未超过 viewport，页面背景为暖白，标题/主层级为紫色，错误态有警示图标和文字 | 将散落颜色收敛到 CSS custom properties；错误态保留安全文案并增加非颜色图标 |
| 桌面完整旅程 | 1280×720 viewport；提交任务 → 等待授权 → 批准 → follow-up → Stop | 通过；composer、timeline、工具卡、approval dialog、Stop 和终止态均可见且可操作 | 将 section heading 允许换行，按钮/状态使用明确文字；Stop 保留红色语义并不依赖颜色表达 |
| 360px 窄屏 | 360×800 viewport；包含授权 dialog 与 Stop | 通过；`document.documentElement.scrollWidth = 345`、`body.scrollWidth = 345`，均未超过可用布局宽度；dialog 316.2px、Stop 282.2px，按钮整宽 | 在 640px 断点切换单列 footer、run controls、dialog actions 与工具事实网格；长 ID/文本允许任意位置换行 |
| 键盘路径 | IAB 实际 Tab/Escape | 通过；dialog 初始焦点为“批准”，Tab 顺序为“批准”→“拒绝”→“稍后处理”→“批准”；Escape 关闭 dialog 但不发决定，并把焦点移到“打开授权对话框”；批准后焦点按原 opener 恢复 | 新增 dialog Tab 环绕、focusout 保护、初始焦点和焦点恢复；focus-visible 使用 3px 南大紫描边 |
| 状态/动态语义 | IAB DOM snapshot | 通过；连接/运行状态有 `role=status`、文字和 `aria-live`；错误有 `role=alert`/assertive；timeline list 为 polite live region；composer/Stop/dialog 暴露 `aria-busy` | 为运行、等待授权、完成、中断、错误等状态补充可见符号和文字，避免只靠颜色 |
| reduced-motion | IAB CSSOM 检查 | 通过路径存在；检测到 `@media (prefers-reduced-motion: reduce)`，其中关闭动画/过渡并恢复非平滑滚动；当前 IAB preference 为 `false`，且本界面没有装饰动画 | 浏览器运行时未提供直接切换 OS reduced-motion 偏好的能力，因此未伪造“强制开启”截图；规则与静态构建已实际检查 |
| 控制台 | IAB `tab.dev.logs` | 通过；未见 error/warning 日志 | — |

## 对比度记录

按实现 token 与实际渲染色值计算，普通文本/控件均达到 WCAG 2.2 AA；大字号标题更高于 AA 要求。关键比例如下：

| 前景 / 背景 | 对比度 |
| --- | ---: |
| `#4D0099` 标题 / `#F7F5F2` 页面底 | 10.87:1 |
| `#241F28` 正文 / `#F7F5F2` 页面底 | 14.83:1 |
| `#51495A` 次要文字 / 白色 surface | 8.57:1 |
| `#6B4F00` 深金文字 / 白色 surface | 7.65:1 |
| 白色文字 / `#4D0099` 主按钮 | 11.83:1 |
| `#4D0099` 次按钮文字 / `#F3ECFA` 紫色浅底 | 10.24:1 |
| 白色文字 / `#8B1E1E` Stop 按钮 | 9.12:1 |
| `#4D0099` focus ring / 白色 | 11.83:1 |

金色 `#C7A34B` 只用于边框/装饰强调；白底文字使用架构规定的深金 `#6B4F00`，不以金色作为普通正文色。

## 自动化证据

- `ApprovalDialog.spec.ts` 新增真实 DOM focus 初始定位、Tab 环绕、focusout 保护、Escape fail-closed 和 opener 恢复断言。
- `npm --prefix web run lint`、`npm --prefix web run type-check`、`npm --prefix web run test`（46 passed）和 `npm --prefix web run build` 均通过。

## 后续 UI 文档核对

2026-09-01 对当前模板、样式和组件测试做了静态核对：消息区无内部固定高度/滚动容器；composer 使用固定定位且发送箭头在运行中禁用；标题栏只保留结束 Session；用户/最终回复左右分列，过程事件默认折叠；错误、SSE 重连、授权、诊断和 Session 入口位于消息尾部；结束 Session 后入口滚动到可见位置。该核对不是新的真实浏览器视觉检查，故未追加桌面、360px、键盘或 reduced-motion 的“通过”结论。
