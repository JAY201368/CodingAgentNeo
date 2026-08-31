# Agent HTTP/SSE 客户端 Binding

这是 CodingAgentNeo Agent 侧 HTTP/SSE adapter 的客户端说明。客户端只需要
`/api/v1` wire contract，不需要导入 Python、了解 `AgentBackend`、读取配置或接触
API Key。通用 adapter 只提供 Agent API，不托管 Web 静态资源。

## 启动

安装 HTTP 可选依赖后，在包含有效本地配置的 Agent 进程中启动：

```bash
python -m pip install -e ".[dev,http]"
coding-agent-neo-http --config .coding-agent-neo.toml
```

服务固定监听 `127.0.0.1`。默认端口为 `8765`，可用 `--port` 修改；启动参数只选择
Agent 配置，不接受 API Key 值。独立 HTTP 入口不提供 Vue/Vite 或 `web/dist` 路由。

## Session 与命令

1. `POST /api/v1/sessions`（空 JSON 对象）创建一个 transport session，响应为
   `201 {"transport_session_id": "...", "state": "RUNNING", "cursor": 0}`。
2. 使用响应中的 opaque `transport_session_id` 调用命令和事件端点。该 ID 不等同于
   event envelope 中的任何 Agent/session/event/correlation/provider ID。
3. `POST /api/v1/sessions/{id}/commands` 只接受四种命令：

   ```json
   {"type":"SubmitTask","text":"检查失败测试"}
   {"type":"ApprovalResponse","request_id":"correlation_...","approved":true}
   {"type":"Interrupt","reason":"user_cancelled"}
   {"type":"CloseSession","reason":"frontend_exit"}
   ```

   成功只表示命令已被接受，响应为 `202 {"accepted":true}`。turn 运行中的第二个
   `SubmitTask` 返回 `409`，不会排队或自动重放。

## SSE 游标

`GET /api/v1/sessions/{id}/events?since=<非负整数>` 返回 `text/event-stream`。每个
canonical event 使用以下三行（随后一个空行）编码：

```text
id: <sequence>
event: agent-event
data: <完整 EventEnvelope JSON>
```

也可发送 `Last-Event-ID`；同时存在时 adapter 使用两者中较大的游标，仅发送
`sequence > cursor` 的事件。空闲时发送 `: keepalive` 注释帧，它不是 Agent event，
不会推进游标。断开 SSE 只停止该消费者，不会批准、拒绝、中断或关闭 Agent。

客户端应在成功处理一条 `data` 后保存其 sequence；重复 sequence 幂等忽略，断线后从
最后成功游标重新订阅。POST 连接失败不能证明命令未被接受，因此客户端不得自动重放。

## 状态、错误与生命周期

`GET /api/v1/sessions/{id}` 返回 `{state,cursor,closed}`。状态值为
`RUNNING`、`WAITING_FOR_APPROVAL`、`COMPLETED_TURN`、`LIMIT_REACHED`、
`INTERRUPTED` 或 `FAILED`；客户端以 `turn_end` 作为 turn 边界，以 `session_end` 或
流结束作为 session 边界。

错误体始终为：

```json
{"error":{"code":"stable_code","message":"safe message"}}
```

常见状态码为 `400`（非法 JSON/命令/游标）、`404`（未知 session）、`409`（已有
活跃 session 或 turn 进行中）、`410`（已关闭 session）和安全化 `500`。错误正文不
包含 traceback、任务正文、provider 响应、配置或 secret。`DELETE /api/v1/sessions/{id}`
幂等关闭并释放唯一活跃 session；服务收到 SIGINT/SIGTERM 时也会有界地关闭后端。

请求 Host/Origin 必须是本地回环来源；服务不提供通配 CORS、公网监听、认证、多用户或
多 session。静态 Web 组合由未来独立 launcher 负责，不属于本 binding。
