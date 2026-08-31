# CodingAgentNeo 需求入口

> 工作流状态：需求与开发过程文档已于 2026-08-28 通过用户审阅；2026-08-31 补充基线范围澄清（介绍视频录制不在本次实现内）
> 权威需求正文：[docs/agent-system-requirements-baseline.md](../agent-system-requirements-baseline.md)
> 当前需求版本：1.2（2026-08-28）

用户指定 `docs/agent-system-requirements-baseline.md` 的完整内容作为本项目的产品需求、范围边界与验收依据。本文件仅作为 `$orchestrate-spec-driven-development` 工作流的需求入口，不复制或改写需求正文，避免形成两个可能漂移的需求副本。

适用规则：

1. 用户最新的明确指令优先于所有仓库文档。
2. 产品目标、功能要求、非功能要求、非目标、首版验收和需求变更规则均以权威需求正文为准。
3. [docs/requirement.md](../requirement.md) 是题目原始材料；当它与已经确认的需求基线存在详略差异时，以需求基线为开发依据。
4. 需求正文发生变更时，必须先同步 `ARCHITECTURE.md`、受影响的 `TASKS.md` 卡片及必要的 `DECISIONS.md` 记录，再开始实现。
5. 2026-08-31 用户明确：`docs/baseline/` 约束的是基线实现而非最终提交版本。介绍/演示视频录制不在本次实现范围；`README.txt` 与演示脚本可以先写一版，后续再迭代。题目原始提交物中的视频要求仍属于最终版本，不得改写需求正文来假装从未要求，也不得把未产出的 mp4 作为本基线任务的通过条件。
