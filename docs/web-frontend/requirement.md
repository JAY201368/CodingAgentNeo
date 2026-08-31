# CodingAgentNeo Web 前端需求

> 状态：用户已明确提出，待按本目录架构与任务卡实施
> 日期：2026-08-31
> Agent 后端规范：[../agent-backend-interface.md](../agent-backend-interface.md)
> Agent 适配层规范：[../agent-transport-interface.md](../agent-transport-interface.md)
> 已完成后端基线：[../baseline/](../baseline/)

## 用户原始需求

`docs/baseline/` 下的项目基线需求已经全部开发完毕，其中 agent 后端的交互规范最初固化在 `docs/frontend-backend-interface.md`，现已按后续澄清重命名并重定位为 `docs/agent-backend-interface.md`。现在需要新增一个极简的轻量 Web 图形化前端，界面简洁、现代、易用；文字、按钮等对象的配色方案使用南京大学官方紫、金配色，占界面大部分的背景使用浅色；技术栈使用 Vite + Vue。

## 用户后续架构澄清

2026-08-31 用户进一步明确：跨进程 HTTP/SSE 适配层属于 Agent 侧的通用传输适配层，必须与 Web 前端分离，且不能以 Vue 或 `web/dist` 为依赖。它应与 CLI 使用的 In-process Adapter 在架构上并列，使 CLI、Web 及未来其他前端平等消费同一套 Agent 命令、事件、状态和授权语义。

当前 baseline 虽已让 CLI 只依赖 `AgentBackend`，但 `backend.py` 仍同时包含抽象端口和 `LocalAgentBackend`、事件缓冲、授权通道等同进程实现。因此本次增量范围必须先增加一张独立任务，把现有 In-process Adapter 显式拆到专门模块，并保持 CLI 行为与公开兼容性；随后再实现与之并列、前端无关的 HTTP/SSE Adapter。Web 前端只实现该网络契约的浏览器客户端和 UI。

## 必须交付

1. 一个基于 Vite + Vue 的可运行 Web 图形化前端，而不是对现成 Agent 产品封装界面。
2. Adapter 遵守 `docs/agent-backend-interface.md` 的后端语义；Web 前端只遵守 `docs/agent-transport-interface.md` 的 HTTP/SSE binding，不越过适配层依赖后端端口或复制 Agent Loop、工具策略、路径安全和持久化逻辑。
3. 在 Agent 侧提供显式 In-process Adapter 和通用 HTTP/SSE Adapter：前者供 CLI 使用，后者供 Web 与未来其他进程外前端使用；两者保持同一语义且不改写 Agent 决策。
4. 支持提交任务、展示 assistant/工具/状态事件、处理执行授权、主动中断、成功 turn 后继续 follow-up，以及终止状态展示。
5. 使用浅色为主的响应式布局；南京大学紫作为主要文字与操作色，金色作为克制的强调色；满足基本可访问性与键盘操作要求。
6. 提供可复现的安装、开发、构建、测试和本地运行说明，不提交 API Key、真实 session、私有路径或构建产物。

## 范围边界

- 保持单 Agent、单活跃 turn、线性 session 和工具串行语义。
- 不新增运行中 steering、消息队列、并行 session、子 Agent、MCP、Skill、远程代码执行或多用户控制平面。
- 不把浏览器直接暴露给模型供应方凭据；API Key 仍只由 Python 后端从环境变量或未入库配置读取。
- 不在本轮工作流初始化中实现产品代码；实现必须在架构与任务 DAG 经审阅后按任务卡串行进行。
- 通用 HTTP/SSE Adapter 不托管、不 import、也不定位 Vue/Vite 构建产物；Web 静态资源与 Agent API 的一键启动只能由独立组合入口负责。

## 验收方向

- 用户能在现代桌面与窄屏浏览器中完成一次端到端任务交互，并清楚区分运行中、等待授权、已完成、受限、中断和失败状态。
- 事件按 sequence 消费；重连或重新订阅不丢失已持久化事实，未知/缺失/截断 payload 不导致页面崩溃。
- 授权请求只回送事件携带的非空 `request_id`；拒绝、超时、断开和 ID 不匹配继续保持 fail-closed。
- 前后端自动化测试、前端 lint/type-check/build 与后端既有质量门通过；真实模型验证与离线 fake 证据明确区分。
