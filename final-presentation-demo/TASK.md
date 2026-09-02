这是一个内存版「图书馆借阅系统」，代码分布在 `library/` 下的 models / repository / rules / service 四个模块，
业务规则的测试在 `tests/test_service.py`。当前有若干测试失败，说明业务规则实现有 bug。

请完成：

1. 先用 bash 运行 `python3 run_tests.py`，看清楚哪些测试失败、失败原因是什么。
2. 阅读相关模块，定位每一个 bug 的根因（不要靠猜，要对照测试期望的业务规则）。
3. 修改源码，使全部测试通过。修复应当符合业务语义：
   - 每位会员最多同时借阅 3 本；借第 4 本必须被拒绝。
   - 借书使可借副本数 -1，还书使其 +1。
   - 逾期滞纳金按天累加，但**不得超过**该书的赔偿价（replacement cost）。
4. 再次用 bash 运行 `python3 run_tests.py`，直到输出 `SUMMARY: 9/9 passed` 且最后一行为 `PASS`（退出码 0）。

约束：
- 不要修改 `tests/` 里的测试来迁就实现；要改的是 `library/` 下的实现。
- 完成前不要声称已经通过；必须实际执行 `python3 run_tests.py` 并看到 `PASS`。
- 修完后用一两句话总结你改了哪几处、分别修了什么 bug。
