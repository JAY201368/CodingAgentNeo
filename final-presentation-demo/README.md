# final-presentation-demo — 视频演示用真实任务

一个内存版「图书馆借阅系统」，用于录制 CodingAgentNeo 的演示视频。

它有一定工程复杂度：领域模型 / 仓储 / 业务规则 / 服务四层分离，并配一套行为测试。
代码里**故意埋了 3 个跨文件的业务 bug**，`python3 run_tests.py` 初始为 `6/9 passed`。
agent 需要探索目录、读多个模块、定位根因、改多处代码、反复跑测试，直到 `9/9 passed / PASS`。

## 目录结构

| 路径 | 作用 |
| --- | --- |
| `library/models.py` | `Book` / `Member` / `Loan` 领域记录 |
| `library/repository.py` | 内存仓储与查询 |
| `library/rules.py` | 借阅规则：借阅上限、借期、逾期滞纳金 |
| `library/service.py` | `LendingService`：借书 / 还书 / 逾期清单 |
| `tests/test_service.py` | 9 条行为测试（描述预期规则，勿改） |
| `run_tests.py` | 无第三方依赖的测试运行器，末行打印 `PASS`/`FAILED` |
| `TASK.md` | 直接粘贴给 agent 的任务正文 |

## 埋入的 3 个 bug（录制者备查，勿写进视频）

1. `rules.can_borrow`：`<=` 应为 `<`，导致会员能借到第 4 本（超限）。
2. `service.return_book`：还书忘记 `book.copies_available += 1`，可借副本数不回升。
3. `rules.late_fee`：滞纳金未用 `min(..., replacement_cost)` 封顶，逾期费会超过赔偿价。

## 推荐启动方式（把 workspace 指到本目录）

CLI（交互式，默认 `approval_mode = ask`，写文件/跑 bash 会弹出批准）：

```bash
coding-agent-neo --workspace temp/final-presentation-demo --approval-mode ask
```

启动后把 `TASK.md` 全文贴进去。

Web GUI（演示前后端解耦更直观，需**重启** Agent 进程并把 workspace 指到本目录）：

```bash
coding-agent-neo-web --config .coding-agent-neo.toml --workspace temp/final-presentation-demo --approval-mode ask
```

然后浏览器里点圆形按钮新建会话，把 `TASK.md` 全文贴进输入框。

## 复位（重录前把项目还原成 buggy 状态）

若上一次 agent 已修好，重录前用 git 丢弃改动即可（本目录在 `temp/`，通常不入库）：

```bash
git checkout -- temp/final-presentation-demo   # 若已纳入版本控制
# 或手动撤销 rules.py / service.py 的 3 处修复
```

修复前 `python3 run_tests.py` 应为 `SUMMARY: 6/9 passed`。
