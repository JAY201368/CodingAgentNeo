CodingAgentNeo 项目说明

仓库地址：https://github.com/JAY201368/CodingAgentNeo

一、项目简介
CodingAgentNeo 是使用 Python 3.12 实现的本地编程智能体。它通过 OpenAI 兼容接口与大模型交互，能自主浏览目录、搜索与读写代码、精确编辑文件、执行命令和测试，并根据真实结果持续推理修正，直至完成修复、开发或分析任务。项目提供 CLI 和 Web 界面，支持权限确认、实时轨迹、历史会话分页与恢复继续。

二、运行方法

1. 需 Python 3.12；Web 界面另需 Node.js 20+ 和 npm 10+。
2. 在项目根目录安装依赖：python -m pip install -e ".[dev,http]"
3. 将 config.example.toml 复制为 .coding-agent-neo.toml，配置 model、api_base、workspace 和 approval_mode。密钥值不得写入仓库或配置文件，只放入 api_key_env 所指定的环境变量。
4. 交互式运行：coding-agent-neo；单次运行：coding-agent-neo --task "任务内容"。
5. Web 运行：先在 web 目录执行 npm ci 和 npm run build，再回到项目根目录执行 coding-agent-neo-web --config .coding-agent-neo.toml，然后访问 http://127.0.0.1:8765。

三、核心设计与特色

1. 自研 ReAct 闭环：项目未使用 Agent 框架，自行实现“调用模型—解析与执行工具—回填结果—继续推理—判断终止”的完整循环。核心采用同步串行状态机，控制流清晰，易于理解、调试和定位故障。
2. 前后端彻底解耦并隔离：CLI 和 Web 只能向 agent 后端发送统一命令、消费统一事件，后端内部实现对前端透明；前者使用进程内适配器，后者使用 HTTP/SSE 适配器。两者共享同一套 Backend 和 Agent Loop，不同类型界面均可基于适配层接口平等接入，互不干扰。
3. Tool-Policy-Environment 三层解耦：Tool 定义工具逻辑，Policy 根据 ask、auto 或 deny 模式决定是否授权且可运行时切换，Environment 负责提供真正的读写和运行命令等带有副作用的能力。Tool 天然无法越界做 Environment 能力之外的操作。新增工具无需改动主循环，替换执行环境无需改动工具实现。内置工具与用户扩展工具拥有统一抽象，允许平等接入与注册。工具注册与激活分离，仅将已激活工具暴露给模型。
4. 可恢复、可审计的事件驱动：事件先追加写入日志再推送给界面，因此用户只能看到已持久化的事实。使用单调序号负责排序和重连，correlation ID 贯通工具请求、授权与结果。恢复会话时可重建上下文、预算和状态；可自动诊断并忽略损坏尾记录。
5. 严格闭合的工具调用：模型一旦声明工具调用，即使随后超限、取消或失败，也会获得唯一合法结果信息，不会导致循环崩溃。
6. 统一的 Agent 运行时管理：将 agent context 视为持久化运行 trace 的投影，实现了上下文压缩机制。该机制始终保留 system prompt，并将 tool call 与 tool result 视为不可拆分的交互组。步数、工具数、协议错误数等由预算跟踪模块统一控制；瞬时错误允许有限重试，认证/配置错误立即停止。每个 Agent 的所有运行状态都归属于独立的 Runtime 实例，不维护全局状态，便于未来 multi-agent 机制的扩展。