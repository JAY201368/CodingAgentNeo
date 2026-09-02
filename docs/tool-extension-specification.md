# CodingAgentNeo 新增 Tool 与运行时注册规范

> 适用范围：为 CodingAgentNeo 增加一个可被模型调用的工具，并使其经过现有的 schema、激活、策略、事件、结果投影和会话恢复边界。
>
> 本文描述当前代码的真实扩展路径；不假设存在动态插件发现、配置文件自动加载或 MCP 注册机制。

## 1. 目标与术语

本文使用以下规范词：

- **必须**：违反后会破坏运行时不变量、安全边界或现有协议。
- **应该**：默认实现方式；偏离时应在变更说明中记录理由。
- **可以**：按工具需要选择。

一个工具从“代码存在”到“Agent 可调用”必须依次满足：

```text
Tool 实现
  → 注册到 ToolRegistry（registered）
  → 加入该 Registry 的 active set
  → AgentRuntime.active_tools 与 active set 完全一致
  → active schema 发送给模型
  → ToolExecutor 校验参数并执行策略
  → Tool 通过 ToolExecutionContext 执行
  → ToolResult 经事件、持久化和模型输出投影返回
```

“已注册”不等于“已启用”。未注册调用返回 `unknown_tool`，已注册但未激活的调用返回 `inactive_tool`；两者都不得产生环境副作用。

## 2. 先选择扩展类型

| 类型 | 适用情况 | 主要改动 |
| --- | --- | --- |
| 纯计算 Tool | 只根据参数计算，不访问文件、网络或进程 | Tool、注册、策略、测试 |
| 复用现有 Environment 能力 | 可由 `read_file`、`list_files`、`search`、`write_file`、`edit_file` 或 `run_command` 完成 | Tool、注册、策略、测试 |
| 新的工作区能力 | 现有 `ExecutionEnvironment` 没有对应操作 | 请求/结果模型、Environment Protocol、所有 Environment 实现、Tool、注册、策略、测试 |
| 外部服务适配 Tool | 调用显式配置的专用外部协议 | 专用窄依赖/传输、Tool、组装注入、注册、策略、测试及凭据脱敏 |

不得为方便而让工作区 Tool 在 `tools/` 中直接使用 `open()`、`pathlib`、`subprocess`、宿主机 `rg` 或其他通用宿主能力。工作区副作用必须下沉到 `ExecutionEnvironment`。外部服务 Tool 只能获得完成该协议所需的专用能力，不得顺带获得任意文件或 shell 能力。

如果新工具只是现有工具的参数别名或固定组合，优先复用原工具，避免扩大模型选择空间和策略表面积。

## 3. Tool 的最小契约

所有工具必须符合 `coding_agent_neo.tools.protocol.Tool`：

```python
class Tool(Protocol):
    name: str
    description: str
    parameters: Mapping[str, Any]

    @property
    def schema(self) -> Mapping[str, Any]: ...

    def validate(self, arguments: str | Mapping[str, Any]) -> Mapping[str, Any]: ...

    def execute(
        self,
        arguments: str | Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult: ...
```

实现必须遵守以下规则：

1. `name` 必须非空、稳定且全局唯一，推荐使用小写 `snake_case`。发布并写入会话后不得随意改名；改名会使旧会话恢复时出现未注册工具。
2. `description` 必须说明“何时使用”和结果语义，避免与已有工具职责重叠。
3. `parameters` 必须是 JSON 可序列化、顶层 `type: object` 的 JSON Schema。
4. `validate` 必须同时接受 JSON 字符串和 Mapping，并在任何副作用之前完成校验。
5. `execute` 必须返回且只返回一个 `ToolResult`，不得返回裸字符串、字典或后端对象。
6. 返回结果必须沿用 `context.correlation_id` 和 `context.provider_tool_call_id`。Registry 会纠正不一致的 ID，但工具实现不应依赖该兜底。
7. 工具必须使用 `context.cancellation`，并把取消、超时及普通失败归一化为相应的 `ToolResultStatus`。
8. `text` 是模型可见输出，应简洁、可操作；`metadata` 必须 JSON 兼容且不得包含密钥、授权头、Cookie、绝对私有路径或不可序列化对象。

项目内置工具应继承 `BuiltinTool`，复用现成的 schema 校验、异常归一化和 Environment 调用包装。符合 Protocol 但不继承该类的实现仍可注册，不过必须自行完整实现上述行为。

### 3.1 当前支持的 JSON Schema 子集

`tools/schema.py` 是轻量校验器而非完整 JSON Schema 引擎。新增 schema 只能依赖当前已实现的关键字：

- `type`：`object`、`array`、`string`、`integer`、`number`、`boolean`、`null`；
- `properties`、`required`、`additionalProperties`；
- `items`；
- `enum`；
- 字符串的 `minLength`、`maxLength`；
- 数值的 `minimum`、`maximum`。

不得假设 `oneOf`、`anyOf`、`pattern`、`format`、`$ref` 等未实现关键字会被校验。跨字段约束应在请求 dataclass 的 `__post_init__` 或工具的 `_execute_validated` 中检查，并在调用 Environment 前转成参数错误。

Schema 应默认设置 `additionalProperties: false`，并为可能产生大输出的参数提供明确上限。

## 4. 推荐实现模板

下面的 `file_stats` 示例复用现有 `read_file` Environment 能力，不直接访问宿主文件系统：

```python
from collections.abc import Mapping
from typing import Any

from coding_agent_neo.environment.base import ReadFileRequest
from coding_agent_neo.models import ToolResult
from coding_agent_neo.runtime import ToolExecutionContext
from coding_agent_neo.tools.builtins import BuiltinTool, _file_result


class FileStatsTool(BuiltinTool):
    name = "file_stats"
    description = "Read one workspace text file and report bounded text statistics."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "max_bytes": {"type": "integer", "minimum": 1},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def _execute_validated(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        request = ReadFileRequest(
            path=arguments["path"],
            max_bytes=arguments.get("max_bytes", 100_000),
        )

        def normalize(response):
            base = _file_result(context, response, requested_path=request.path)
            if not base.ok:
                return base
            return ToolResult(
                correlation_id=context.correlation_id,
                provider_tool_call_id=context.provider_tool_call_id,
                status=base.status,
                text=(
                    f"lines={len(response.content.splitlines())}, "
                    f"characters={len(response.content)}"
                ),
                metadata={"source_truncated": response.truncated},
                duration_seconds=base.duration_seconds,
                path=base.path,
            )

        return self._call(context, request, context.environment.read_file, normalize)
```

该示例用于说明边界，不要求每个工具都加入 `builtins.py`。如果内部辅助函数（如 `_file_result`）需要被多个模块使用，应先提升为有测试的公共 helper，而不是跨模块长期导入私有名称。

## 5. 注册与激活

### 5.1 项目内置 Tool（默认生产运行时可用）

当前生产组装层在 `assembly.py` 中调用 `default_tool_registry()`，因此让一个新内置工具进入 CLI、HTTP 和 Web 运行时，必须完成以下修改：

1. 在 `tools/builtins.py` 定义稳定名称常量和工具类。
2. 将名称加入 `BUILTIN_TOOL_NAMES`。
3. 将类加入 `BUILTIN_TOOL_TYPES`；`builtin_tools()` 将据此创建新实例。
4. 按公共 API 需要更新 `tools/builtins.py` 和 `tools/__init__.py` 的导出。
5. 更新 `policy.py`，为新名称给出显式的 `allow`、`ask` 或 `deny` 规则。当前 `DefaultExecutionPolicy` 对未知名称一律 `deny`，只注册而不更新策略会得到 `DENIED`。
6. 确认 `default_tool_registry()` 注册并激活该工具。生产组装会从 `registry.active_names` 构造 `AgentRuntime.active_tools`，无需在两处手写另一份名称列表。

名称列表与类型列表必须顺序一致。顺序决定发给模型的 schema 顺序，也用于稳定测试；禁止使用无序集合构造默认 schema。

### 5.2 显式注入的自定义 Tool

测试、嵌入式调用或自定义 composition root 可以显式构造 Registry：

```python
registry = ToolRegistry([FileStatsTool()], active_tools=("file_stats",))
runtime = AgentRuntime(
    agent_id="agent-1",
    session_id="session-1",
    environment=environment,
    execution_policy=policy,
    active_tools=set(registry.active_names),
)
loop = AgentLoop(model_client, registry, event_emitter, runtime, system_prompt=prompt)
```

构造 `AgentLoop` 前，以下等式必须成立：

```python
frozenset(runtime.active_tools) == registry.active_tools
```

不一致会触发 `ActiveToolsMismatchError`。运行期间也不得只修改其中一边；当前实现没有运行中热注册/热切换协议。

当前公开的 `build_agent_backend()` 没有 `registry` 或 Tool provider 参数，配置文件也没有工具发现字段。因此外部 Tool 若要进入标准应用运行时，必须先在 `assembly.py` 增加一个显式、可测试的注入 seam，并让下列路径统一使用同一 Registry 工厂：

- 新会话构建；
- 恢复会话构建；
- `build_agent_backend_provider()` 的恢复预检。

不得只修改新会话路径。会话的 `agent_start.active_tools` 会被持久化；恢复时，历史 active tool 中任何名称未在当前 Registry 注册都会使恢复失败。

### 5.3 注册不变量

- 重名注册默认必须失败；只有明确迁移场景才可使用 `replace_existing=True`。
- 替换已有工具会使其退出 active set，之后必须显式重新激活并同步 Runtime。
- 只有 active schema 可以发给模型；不得另建“所有已注册 schema”旁路。
- Tool 来源（内置、外部适配器或未来扩展）不得在 Agent Loop 或 Tool Executor 中形成分支。

## 6. 策略与审批

Registry 解决“是否存在、是否暴露”，Execution Policy 解决“本次调用是否允许执行”。两者缺一不可。

新增 Tool 必须先分类：

- 无副作用且输入受控的只读/纯计算操作，可以默认 `allow`；
- 修改状态、启动进程、访问网络、产生费用或发送数据的操作，应该走 `_decide_side_effect()`，在 `ask` 模式请求审批；
- 路径、目标、凭据来源或参数不安全时必须 `deny`；
- 策略异常必须 fail closed，不得在策略失败后继续执行。

策略检查必须基于已经 schema 校验的参数，同时补充 JSON Schema 无法表达的安全规则，例如相对路径、NUL、跨字段关系和目标 allowlist。审批只授权当前一次规范化调用，不能作为后续调用的永久授权。

若工具需要新的权限模型，优先扩展策略的显式分类/规则；不得在 Tool 内部自行弹窗、读取 stdin 或绕开 `ToolExecutor`。

## 7. 增加新的 Environment 操作

只有当现有六个逻辑操作无法表达需求时，才扩展 Environment。必须按以下顺序完成：

1. 在 `models.py` 增加后端无关、不可变的 Request/Result dataclass；在 `__post_init__` 校验类型、范围、逻辑路径和跨字段约束。
2. 在 `environment/base.py` 的 `ExecutionEnvironment` Protocol 增加方法，并更新导出。
3. 在 `environment/local.py` 实现生命周期检查、工作区边界、取消、超时、输出上限和错误归一化。不得从 Result 泄漏本地绝对路径、进程句柄或后端专有对象。
4. 更新所有 Environment 实现及 fake；Protocol 是整体契约，遗漏一个实现会破坏结构化兼容性。
5. Tool 只把已校验参数翻译为 Request，并把 Environment Result 翻译为 `ToolResult`。
6. 增加 Environment 契约、Local 实现和 Tool 翻译的分层测试。

不得把供应商 SDK response、HTTP client 或本地实现类型放入通用 Request/Result。若能力仅属于某个外部服务，应定义专用窄端口，而不是污染通用工作区 Environment。

## 8. 输出、事件与敏感信息

正常路径必须由 `AgentLoop → ToolExecutor → ToolRegistry → Tool` 调用。这样系统会自动产生关联的 `tool_call`、`policy_decision` 和 `tool_result` 事件，并应用模型可见/持久化输出上限。

工具实现仍必须主动控制输出：

- 大结果在最靠近数据源处截断，并设置 `truncated` 与 `original_length`；
- 错误消息不得回显完整敏感参数；
- metadata key 应稳定且可 JSON 序列化；
- 不得记录 token、password、secret、API key、Authorization、Cookie 或私钥；
- 不得绕过事件发布直接把结果塞入模型上下文或 Session Store。

如果新增字段会进入前端展示，先确认现有通用 ToolCard/事件协议能表达；只有改变 wire contract 时才需要同步修改 Web domain 类型和传输契约。

## 9. 测试要求

每个新增 Tool 至少必须覆盖：

1. schema 可 JSON 序列化，名称和必填字段正确；
2. 注册但未激活时不暴露 schema、不可执行；
3. 激活后 schema 进入模型请求；
4. 合法参数被正确翻译，且传入的是同一个 cancellation signal；
5. 非法 JSON、缺字段、错类型、未知字段和跨字段错误均不调用 Environment；
6. `SUCCESS`、`ERROR`、`CANCELLED`、`TIMEOUT` 的结果映射；
7. correlation ID 与 provider tool call ID 保持；
8. 策略的 allow/ask/deny 路径及拒绝时零副作用；
9. 生命周期事件严格配对，模型可见输出和持久化输出受到上限；
10. 标准组装的新会话可见该工具，且包含该工具的会话能够恢复。

新增 Environment 操作还必须测试工作区逃逸、符号链接边界、NUL、生命周期未启动/已关闭、取消、超时和输出截断（按该操作适用项选择）。外部 Tool 还必须测试连接失败、服务端超时、凭据脱敏、重试幂等性和副作用审批。

建议把测试分别放在：

- `tests/unit/tools/`：schema、参数校验、Tool 翻译和 Registry；
- `tests/unit/environment/`：Local Environment 行为；
- `tests/unit/test_policy.py`：策略分类；
- `tests/integration/`：模型 schema、执行生命周期、事件和恢复；
- `tests/architecture/`：禁止依赖与宿主能力边界。

提交前至少运行：

```bash
python -m pytest tests/unit/tools tests/unit/test_policy.py
python -m pytest tests/integration/test_tool_lifecycle.py tests/integration/test_agent_loop.py
python -m pytest tests/architecture
python -m ruff check src tests
python -m ruff format --check src tests
```

改动 Environment、组装、会话恢复或传输契约时，必须再运行完整测试：

```bash
python -m pytest
```

## 10. 评审清单

合并新增 Tool 前逐项确认：

- [ ] 名称稳定、唯一，description 明确且与现有 Tool 不重叠。
- [ ] 顶层 schema 为 object，只使用项目校验器支持的关键字，并限制额外字段和输出规模。
- [ ] 参数和跨字段约束在任何副作用之前校验。
- [ ] Tool 没有直接宿主文件、搜索或进程访问；能力来自显式 context/窄端口。
- [ ] 所有路径、超时、取消、截断和错误均有确定语义。
- [ ] 返回标准 `ToolResult`，ID 一致，metadata 可序列化且无敏感信息。
- [ ] Tool 已注册且按预期激活；Registry 与 Runtime active set 完全一致。
- [ ] 默认策略已显式识别该 Tool，副作用工具不会被静默放行。
- [ ] 新会话、标准前端和恢复预检使用同一 Registry 定义。
- [ ] 单元、集成、架构及必要的恢复测试通过。
- [ ] 未在 Agent Loop、ToolExecutor 或前端加入按 Tool 来源分支。

## 11. 常见失败模式

| 现象 | 原因 | 修正 |
| --- | --- | --- |
| 模型看不到新工具 | 只注册，未激活 | 将工具加入当前 Registry active set，并同步 Runtime |
| 构造 Loop 时报 active tools mismatch | Registry 与 Runtime 是两个不一致的可变事实源 | 从 `registry.active_names` 一次性构造 Runtime 集合 |
| 调用结果为 `DENIED` | 默认策略不认识新名称 | 增加显式策略分类与参数安全检查 |
| CLI/Web 中不存在，但单测可调用 | 只在测试 Registry 注册 | 更新默认 built-in 定义，或为组装层增加显式 Registry 注入 seam |
| 新会话正常、旧会话无法恢复 | 恢复预检使用不同 Registry，或工具被改名/移除 | 统一 Registry 工厂；为名称生命周期制定兼容策略 |
| 非法参数仍产生副作用 | 跨字段校验放在 Environment 调用之后 | 移到 schema、请求 dataclass 或 `_execute_validated` 的调用前阶段 |
| 架构测试失败 | Tool 直接 import 宿主 I/O 模块 | 把实际能力移入 Environment 或专用窄端口 |
| schema 声明了约束但未生效 | 使用了轻量校验器不支持的 JSON Schema 关键字 | 改用受支持关键字或显式校验 |

## 12. 相关代码入口

- Tool Protocol：`src/coding_agent_neo/tools/protocol.py`
- 参数解析与 JSON Schema 子集：`src/coding_agent_neo/tools/schema.py`
- Registry、active set 与 dispatch：`src/coding_agent_neo/tools/registry.py`
- 内置 Tool 实现与默认列表：`src/coding_agent_neo/tools/builtins.py`
- 执行策略：`src/coding_agent_neo/policy.py`
- Tool 生命周期、审批与事件：`src/coding_agent_neo/executor.py`
- Runtime 与 ToolExecutionContext：`src/coding_agent_neo/runtime.py`
- Environment Protocol / Local 实现：`src/coding_agent_neo/environment/base.py`、`src/coding_agent_neo/environment/local.py`
- 默认生产组装与恢复预检：`src/coding_agent_neo/assembly.py`
- active schema 进入模型的位置：`src/coding_agent_neo/agent_loop.py`
