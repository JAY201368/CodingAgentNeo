# CodingAgentNeo 基线验收 Runbook

本文件说明如何复现 AC-01～AC-14。自动化证据在 `tests/acceptance/` 聚合引用既有测试，而不是整文件复制。显式 system prompt 与通用 Tool 注入只证明这两条窄扩展边界，**不表示 Skill 或 MCP 已实现或已验证**。

## 1. 自动化套件

在仓库根目录、Python 3.12 虚拟环境中：

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/acceptance -m acceptance
.venv/bin/python -m build
```

2026-08-31 worker 实测：`ruff check` 通过；`ruff format --check` 通过（81 files already formatted）；全量 `pytest` **262 passed**；`pytest tests/acceptance -m acceptance` **50 passed**（不再因 0 项返回 exit 5）；`python -m build` 产出 sdist/wheel。
AC-02～AC-14 的代表性自动化证据见 `tests/acceptance/ac_catalog.py`。`pytest tests/acceptance -m acceptance` 会：

- 在 `tests/fixtures/buggy_counter` 的副本上用 scripted fake model + `LocalExecutionEnvironment` 跑完 AC-01 六步闭环；
- 注入非内置 fake Tool `record_note`，且 Loop 源码不含该名称或 Skill/MCP 分支；
- 重新调用既有单元/集成/安全/架构测试作为 AC-02～AC-14 证据；
- 扫描已跟踪文件，确认无真实 API Key 形态和未忽略的 session JSONL。

## 2. AC-01 真实网关演练（可选，不能用 mock 冒充）

自动化套件**不**调用真实模型。若要在真实 OpenAI-compatible 网关上复现 AC-01：

1. 不要把 API Key 写入仓库、TOML、argv 或 JSONL。只导出环境变量名（默认 `OPENAI_API_KEY`）。
2. 使用已 gitignore 的 `.coding-agent-neo.toml` 填写 `model`、`api_base`、`api_key_env`。
3. 把小型缺陷仓库复制到 gitignore 的 `temp/`，例如：

```bash
rm -rf temp/ac01-buggy-counter
cp -R tests/fixtures/buggy_counter temp/ac01-buggy-counter
.venv/bin/python -m coding_agent_neo \
  --config .coding-agent-neo.toml \
  --workspace temp/ac01-buggy-counter \
  --session-dir temp/ac01-buggy-counter/.coding-agent-neo/sessions \
  --approval-mode auto \
  --task "Explore the workspace, search for increment, fix counter.py so python verify.py passes, and summarize."
```

把 `session-dir` 放在 workspace 内时，`search` 可能命中正在写入的 JSONL 并在终端刷出超长行。演示时更稳妥的做法是把 `--session-dir` 指到 workspace 外的 gitignored 目录。

4. 在脱敏记录中只保留：模型名、网关主机（不含路径查询）、UTC 时间、退出码、是否完成六步。不要保存 Key、Authorization、完整 JSONL 或私密绝对路径。
5. 真实 session JSONL 必须留在 gitignore 目录，不得提交。

### 本次执行记录（2026-08-31）

| 项 | 结果 |
| --- | --- |
| 模型 / 网关主机 | `kimi-k3` / `dashscope.aliyuncs.com` |
| 时间 | `2026-08-31T01:16:14Z`（UTC；北京时间 09:16） |
| 命令 | 用户在仓库根目录按本节命令手动执行（含 `--config .coding-agent-neo.toml`） |
| 结束状态 | `COMPLETED_TURN`，`steps=5`，`tools=7`；非交互 stdout 给出无 tool call 总结 |
| 工作区结果 | `counter.py` 由 `return value + 2` 改为 `return value + 1`；`python verify.py` 打印 `ok`、退出码 0 |
| AC-01 六步 | 探索、搜索、修改、验证、无 tool call 总结均发生。模型第一次编辑即正确，**未**先跑失败再修正；该步仅由 scripted 套件证明 |
| Session | `session_70b2054acc464bfeafc9145a7d535fe7`（gitignored `temp/`，未入库） |
| 凭据 | 终端与记录中无 API Key / Authorization |
| 观察问题 | `search "increment"` 命中 workspace 内 session JSONL，终端出现超长 JSON 行；resume 提示带了绝对 `--session-dir` |
| AC-01 自动化证据 | scripted fake model + LocalEnvironment，见 `tests/acceptance/test_ac01_closed_loop.py` |

## 3. 验收对照

权威条文是 `docs/agent-system-requirements-baseline.md` 第 20 节。不要改写成功标准。

| AC | 自动化或可复现证据 | 本次结果 |
| --- | --- | --- |
| AC-01 | scripted 六步闭环 + fake Tool 注入；真实 API 按第 2 节 | 自动化 passed；真实 API **手动完成**（未走“失败后修正”） |
| AC-02 | `test_agent_limits` 连续协议错误；registry 非法参数 | 自动化 |
| AC-03 | Loop 编辑失败后修正；命令 timeout；Local 非零退出 | 自动化 |
| AC-04 | `tests/security/test_workspace_boundary.py` | 自动化 |
| AC-05 | policy 默认对写操作与 bash ask；CLI 交互确认/拒绝；auto 无人值守 | 自动化 |
| AC-06 | AC-01 JSONL；session store；中断轨迹 | 自动化 |
| AC-07 | session recovery + resume CLI，不重放副作用 | 自动化 |
| AC-08 | 小窗口 compaction；显式 system prompt 计入预算 | 自动化 |
| AC-09 | model-step / tool-call / protocol / wall `LIMIT_REACHED` | 自动化 |
| AC-10 | 跟踪树扫描 + 事件/模型/配置脱敏 | 自动化 |
| AC-11 | fake Environment 内置工具；仅 LocalEnvironment 含 subprocess | 自动化 |
| AC-12 | 双 Runtime 隔离；Loop/Runtime 无进程级可变运行状态 | 自动化 |
| AC-13 | correlation/provider ID；AC-01 同 agent ID | 自动化 |
| AC-14 | Local 生命周期与六类操作；Loop/Tool 不依赖 Local 类型 | 自动化 |

## 4. 限制

- 真实网关已由用户于 2026-08-31 手动跑通一次（`kimi-k3` / `dashscope.aliyuncs.com`）。不能把 scripted fake model 说成真实 API；也不能把这次“一次改对”说成已观察到“根据失败结果修正”。
- 当 `session-dir` 位于 workspace 内时，`search` 会索引 JSONL 并可能刷屏；T13 演示应把 session 目录放在工作区外。
- LocalEnvironment 只在当前 macOS Python 3.12 上由组件测试覆盖；shell 继承宿主用户权限，不是 OS 沙箱。
- 未验证 Linux/Windows、Docker、子 Agent、Skill/MCP、Web GUI 或完整 TUI。
- 注入的 fake Tool 与显式 system prompt **不是** Skill/MCP 实现证据。
