# 停止后的工具协议与本人审批

2026-10-24 00:37（北京时间），main-v38候选，承接b61dd6d。第一阶段未通过，第二阶段未部署；本轮不启动北极星后台。

## 证据先于结论

旧_close_dangling只在最后消息是AI工具调用时补结果，使用整个历史的tool_call_id判断是否回答，且把缺失结果写成默认success状态的“没有执行”。另外，不检查尚待本人确认的HIL中断；停止的_run最后直接丢弃state，任务收尾看不到审批。

修前9项检查5失败、4通过。已证明的失败：构造的部分结果检查点没有补齐；旧轮相同ID错误抵消新轮调用；缺失被写成未执行且success；实际HIL检查点在修复中被改写；实际HIL发生后停止时_run没有返回待确认动作。这些分别是协议状态注入和实际图/HIL检查，不是已发生于真实求职网页的事故。

专门注入实际并行查询的b结果pending-write失败，两次合成工具均已执行，框架仍物化保存了a／b两条success结果。**这一轮没有重现部分结果丢失**，不把“写入报错”直接等同丢结果。该例作为保留完整结果、不重放工具的控制；部分结果覆盖来自手工构造的实际图检查点，不能冒充自然并行运行的捕获。

## 修改

1. 只查看当前末尾工具批：从末尾ToolMessage回溯到对应AI；已返回结果只在本批计算，不使用旧轮相同ID。不跨越新用户消息或最终回答去补历史。
2. 缺失结果补error协议占位，注明“执行结果在检查点中未确认，不能据此断言未执行或成功，不要自动重放可能已生效操作”。不调用工具、不更改已返回正文，也不把占位虚构成真实runtime工具调用／用量。
3. 有实际审批中断时不改检查点，停止不替用户拒绝／批准。_run在停止收尾可读到审批时保留messages和interrupts；_turn及TaskService把待本人决定的动作交回现有waiting_approval路径。
4. 图无法读回时不猜审批或执行动作。这个故障出口尚未做PG／网页恢复验证，不能承诺停止后任何存储故障都无缝恢复。

工程依据：本机langchain 1.4.4 agents/factory.py对每个pending调用发Send("tools")、按尾部AI／Tool结果决定下一节点；langgraph 1.2.14和InMemorySaver实际图行为。没有新框架、表、HTTP接口或配置。当前补齐仅覆盖末尾工具批，不是任意损坏历史的自动修复器。

## 验证

| 范围 | 结果及限制 |
|---|---|
| 9项协议／实际图检查 | 修前5失败4通过，修后全通过；包括审批不改写和实际停止后保留审批 |
| 部分结果状态 | 实际图update_state构造一条已返回、一条缺失；补齐后假模型按独立协议约束核对每个调用各有结果，下一轮可继续，工具执行计数不变；未声称真实平台发生过此状态 |
| 实际并行故障控制 | 两个真实ToolRuntime合成调用均发生；注入pending-write失败后a／b均已保存，原正文和success保留，下一轮不重放；不是部分丢失证据 |
| 队列→实际chat→HIL→停止→正式记录 | 新增一例：pending动作仍在Thread和Task.result同一审批列表，waiting_approval／STOPPED／cancel_requested，未写正式回答，副作用执行0；假模型／记忆、内存SQLite，有限记忆线程串行等待 |
| 相邻集合 | 162通过、3项数据库检查未选、0跳过，Ruff通过，未处理线程告警按错误处理 |

命令：

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest tests/test_stopped_tool_protocol.py tests/test_task_delivery.py tests/test_task_cancellation.py tests/test_report_lifecycle.py tests/test_demo.py tests/test_config.py tests/test_search.py tests/test_matching_constraints.py tests/test_career_report.py tests/test_requirements_review.py tests/test_question_context.py tests/test_interviews.py -k 'not direct_report_is_saved and not matching_memory_date and not recent_search_excludes_updates' --tb=short -W error::pytest.PytestUnhandledThreadExceptionWarning --junitxml=data/probe/stopped-tool-protocol-regression-20261024.xml
.venv\Scripts\python.exe -X utf8 -m ruff check src/polaris/agent/main.py tests/test_stopped_tool_protocol.py tests/test_task_delivery.py
```

修前／修后证据：data/probe/stopped-tool-protocol-before-20261024.xml、stopped-tool-protocol-after-20261024.xml、stopped-tool-protocol-regression-20261024.xml。修前XML为纠正并行故障测试假设后的9项5失败4通过；最初将b应缺失／error写入预期并不符合实际，已修正该测试，没有为让预期通过而改掉已成功结果。

## 运行与剩余交付

00:36核实Polaris常驻任务Disabled／Enabled=false；5432、55432、8000、8010、3000、3010、8001、3001、9222、9223无监听。本轮没有服务启动、真实模型请求、改.env／网络，候选未加载。原六份dirty文档与10-22三份未跟踪资料保留。

真实PG／网页停止、审批批准／存储失联恢复、旧无标签／非队列上下文仍需验收。完整任务、重复／预留请求、独立干净启动和两分钟演示仍欠；162项不能替代第一阶段12项验收。接下来先检查独立演示的离线迁移／配置和运行说明是否一致，继续补可离线验证的交付缺口；不据此自行启动服务。
