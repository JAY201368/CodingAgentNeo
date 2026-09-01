# CodingAgentNeo 前端接入与 Web 前端 Agent 工作协议

`requirement.md` 控制增量目标；`ARCHITECTURE.md` 控制边界；`../agent-backend-interface.md` 控制 adapter 面向的 Agent 后端端口；`../agent-transport-interface.md` 控制前端接入 binding；`TASKS.md` 控制任务范围和依赖。T01 已交付共享 Backend Service 与 In-process binding；后续任务仍按 DAG 串行执行。

## 1. 开始工作前

1. 完整阅读本文件、需求、架构、进度、当前任务和依赖摘要。T01/T02 必须阅读两份接口规范；T03 无需阅读任何 Python/adapter 接口；T04～T09 需要且只需要参考 `../agent-transport-interface.md` 中适用的 HTTP binding、公开事件目录和共享规则，不读取后端 Port 文档来补充前端契约；T10 为审计 Port/Service/adapter 纯度与 conformance，必须阅读两份规范。
2. 检查 `git status` 与相关文件；保留用户/其他 Agent 的无关改动，范围重叠先报告。
3. 从 `[x]` 和仓库证据确认依赖，不得为未完成依赖发明替代实现。
4. 修改前一句话复述当前任务范围和排除项，一次只处理一个任务 ID。
5. 每个任务必须由全新专用子 Agent 实施；不得领取、开始或继续后续任务。
6. 端口、wire、状态、approval、安全或部署契约不完整时停止实现，先走变更控制。

## 2. 标准命令

下列是目标命令。T01 建立显式 In-process 模块但复用既有 Python 门；T02 建立 HTTP 命令；T03 建立 Web 命令；T09 建立 Web 组合入口。对应任务接受前不得声称新命令可用。

| 命令 | 用途 | 建立/复用 |
| --- | --- | --- |
| `.venv/bin/python -m pip install -e ".[dev]"` | Python 基线开发安装 | baseline 已建立，T01 复用 |
| `.venv/bin/python -m pytest` | Python 全量测试 | baseline 已建立 |
| `.venv/bin/python -m ruff check .` | Python lint，不改写 | baseline 已建立 |
| `.venv/bin/python -m ruff format --check .` | Python格式检查，不改写 | baseline 已建立 |
| `.venv/bin/python -m build` | Python 构建 | baseline 已建立 |
| `.venv/bin/python -m coding_agent_neo --help` | CLI/In-process 入口 | baseline 已建立，T01 回归 |
| `.venv/bin/python -m pip install -e ".[dev,http]"` | HTTP adapter 可选依赖 | T02 |
| `.venv/bin/coding-agent-neo-http --help` | 独立 Agent HTTP 入口 | T02 |
| `.venv/bin/python -m pytest tests/transports` | adapter/conformance 测试 | T01/T02 |
| `npm --prefix web ci` | 按 lockfile 安装 Web 依赖 | T03 已建立（2026-09-01 实测） |
| `npm --prefix web run dev` | Vite 开发服务 | T03 已建立（2026-09-01 实测） |
| `npm --prefix web run lint` | Web lint，不改写 | T03 已建立（2026-09-01 实测） |
| `npm --prefix web run type-check` | Vue/TypeScript 类型检查 | T03 已建立（2026-09-01 实测） |
| `npm --prefix web run test` | Web Vitest | T03 已建立（2026-09-01 实测） |
| `npm --prefix web run build` | 生成 `web/dist` | T03 已建立（2026-09-01 实测） |
| `.venv/bin/coding-agent-neo-web --help` | Web composition launcher | T09 |
| `.venv/bin/python -m pytest tests/acceptance -m acceptance` | baseline 聚合验收 | baseline 已建立，T10 必跑 |

只对当前任务明确路径运行 formatter/自动修复；不得全仓机械重写。前台服务以 Ctrl+C 停止，不编造后台 PID 管理脚本。

## 3. 模块与依赖边界

- `src/coding_agent_neo/backend.py` 是纯应用端口；不得拥有 thread/queue、Loop、Store、EventEmitter、CLI 或 HTTP。
- `src/coding_agent_neo/backend_service.py` 拥有 `AgentBackendService`、worker、EventStreamBuffer 和 ApprovalChannel/Port；不得读写 CLI/HTTP，也不得依赖任何 adapter。
- `src/coding_agent_neo/transports/in_process.py` 拥有薄 `InProcessAdapter`、Python binding、同进程生命周期和 resume 元数据暴露；不得拥有 worker/Core 执行语义或读写终端/HTTP。
- `src/coding_agent_neo/transports/http/` 拥有 wire/SSE/registry/error/security 映射；不得依赖 In-process Adapter、Vue、Vite、`web/dist` 或 Core 具体对象。
- `assembly.py` 提供共享 backend factory，并只在 composition root 组装具体 adapter；`cli.py` 只用 In-process composition factory；`http_cli.py` 只组合 Agent HTTP 服务与共享 backend factory；`web_launcher.py` 才可同时看见 HTTP app 与静态资源。
- Vue 产品代码只在 `web/`；只有 `web/src/api/` 发网络请求，只有 session composable 管理 transport ID/cursor。
- CLI 行为和 baseline 测试必须保持兼容；adapter 拆分不得顺手重构 Agent Loop/Tool/Policy/Environment/Session。

## 4. 代码与契约约定

- Python 3.12 公共边界有类型标注；新代码使用 `AgentBackendService`/`build_agent_backend` 和 `InProcessAdapter`/`build_in_process_adapter`，旧名称仅作无分叉兼容 facade。
- command/type/state/event/sequence/approval 逐字符合语义规范；HTTP 路径、错误、SSE 和 session 符合传输规范。
- transport ID 与 Agent session/agent/event/correlation/provider ID 永不混用；POST 不自动重放或排队。
- HTTP 只监听 `127.0.0.1`、验证 Host/Origin且无通配 CORS；扩大暴露前必须设计认证等新安全契约。
- TypeScript strict；组件 `PascalCase.vue`，composable `useXxx`，变量/函数 `camelCase`，CSS token `--kebab-case`。
- Vue 使用 Composition API、明确 props/emits；首版不新增 Router、Pinia、UI 框架或巨型运行时 schema 依赖。
- payload 按不可信 JSON 防御性读取；未知/截断降级，禁止未清洗 `v-html`，不得执行 tool/arguments。
- API Key 只从 Agent 进程环境读取，禁止进入 argv 值、HTTP/SSE、浏览器、日志、fixture、snapshot、截图或文档。

## 5. 变更类型与最低验证

| 变更 | 最低验证 |
| --- | --- |
| 流程/架构文档 | 链接、路径、代码块、需求追踪、DAG、两个接口一致性和 skill validator |
| Port/Backend Service/In-process | service + adapter conformance、CLI/approval/resume 回归、静态 import、Python 全量门 |
| HTTP/SSE | fake + real shared backend factory、HTTP 不经 In-process 的静态/集成证据、wire/status/cursor/断开/关闭/Host/Origin/脱敏、shared conformance |
| TypeScript API/domain | success/非法/未知/截断/重复/跳号/网络失败 + lint/type/build |
| Vue UI | 组件交互、键盘/aria、错误/边界 + lint/type/test/build |
| 视觉 | 组件/build + 桌面/360px/键盘/对比度/reduced-motion 人工检查 |
| 组合/打包 | 独立 HTTP 与 Web launcher 启动、路由优先级、缺 dist、包内容和全量回归 |
| 最终交付 | shared conformance、Web 全量门、Python 全量门、baseline acceptance、validator |

任务卡更严格时以任务卡为准。未运行、跳过或环境失败必须原样报告。

## 6. 禁止事项

- 不提交 secret、真实 session、任务正文日志、私有路径、本地配置、node_modules、dist、coverage、venv 或大生成物。
- 未经授权不执行破坏性 git/文件命令，不发布、不推送、不启动公网服务、不对外发送任务或凭据。
- 不让 HTTP adapter import In-process/Web、不让 Web import Python、不让 backend port/service import adapter，不复制 Agent Policy/路径安全/持久化。
- 不自动批准、不从 summary 重建命令、不把断线视为批准/中断、不排队第二 turn。
- 不用 mock 声称真实模型、真实浏览器网络或 shell 隔离通过。
- 不扩大当前卡片、顺手做下一任务、修改无关 baseline、降低质量门或把未运行写成通过。

## 7. 交付报告与勾选

Worker 报告必须包含任务 ID/范围、变更模块、可观察行为、实际命令和逐项结果、Acceptance checklist、端口/wire/配置/安全/兼容/下游影响及限制；按事实更新 `PROGRESS.md`，仅在持久非显然选择时追加 `DECISIONS.md`。

主 Agent 独立审阅 diff 和证据。全部验收满足后才勾选并追加日期、行为、边界和真实结果；随后关闭该任务专用 Agent。
