# 未交付报告与下一轮模型上下文

2026-10-24 00:26（北京时间），main-v37候选。承接198f0d3的任务停止收尾；第一阶段仍未通过，第二阶段未部署。没有启动采集、数据库、API、网页或专用浏览器。

## 可复现的问题

任务队列已经能把停止后的迟到报告记为cancelled且不写正式聊天记录，但LangGraph可能先把报告存进检查点。下一轮直接读取该检查点时，模型仍能看到这份用户没有收到的推荐。

在实际LangGraph和InMemorySaver上生成报告，给真实图产出的AI消息补上测试归属标签、在内存任务表标记取消，然后捕获第二轮假模型的实际输入：旧实现把“未交付的合成报告：应当选择演示岗位甲。”发送给了模型，检查失败。人工补标签仅用于隔离“已有归属但未核交付”的失败，不能说旧代码原本就有任务标签。后续检查另覆盖了TaskService→真实chat→图→正式记录→下一轮整段代码，验证生产调用确实传入并保存归属。

## 修正与工程依据

- 队列把自身task_id传给内部chat／resume；模型输出在模型节点落检查点之前关联该编号，程序写回的最终报告也关联编号。采用wrap_model_call处理返回值，避免after_model另一个节点尚未执行时被停止，留下无标签消息。
- 新一轮before_agent先读取同会话任务及正式ChatMessage。生成结果和completed字段都不是独立交付证明；同task_id的正式assistant记录才是依据。
- 没有交付记录的带归属AI回答换为明确的内部未交付说明；带工具调用的中间文字清空，调用及ID保留；career_report的工具正文也遮去。普通工具证据和用户原话保留，不回滚已生效操作或重放工具。
- 这个处理先于摘要before_model，避免未交付推荐进入新的摘要。修改追加新检查点版本，旧版本仍可审计；不删除历史检查点。
- 归属记录缺失、跨会话、先前任务仍活动或数据库无法核实，直接停止本次回答模型调用并给固定错误，不把原始SQL／异常内容发给用户。不自动重新生成。
- 旧历史导入跳过带任务标签的生成消息和内部状态消息；这类任务的正式文本应由独立ChatMessage交付，不能从旧检查点把曾生成过的内容重新导入成“用户见过”。已有正式记录不删除。

实现沿用既有LangChain中间件，无新依赖、数据表、HTTP接口或模型调用。依据为已安装的主框架源码：langchain 1.4.4 agents/middleware/types.py的ModelResponse／ExtendedModelResponse与before_agent契约，agents/factory.py的before_agent→before_model及wrapper组合顺序；实际图检查核对这些行为。当前langgraph 1.2.14、langgraph-checkpoint 4.2.0、SQLAlchemy 2.1.4，不拿其他版本文档代替本机行为。

## 验证范围

| 范围 | 当前证据 |
|---|---|
| 原始重现 | 修改前1项失败；实际下一轮模型请求包含未交付报告，XML task-delivery-before-20261024.xml |
| 实际队列交付与下一轮 | 取消／正常完成各一例；取消后不发送报告，正式已交付报告继续保留；真实chat调用但假模型、假记忆和空卡片投影，memory_first串行等待有限记忆线程 |
| 停止窗口 | 模型节点刚返回／报告工具返回后停止均覆盖；归属已保存、工具正文不泄入下一轮，调用配对保留 |
| 普通回答 | 非工具AI消息也有关联；completed且result含answer但无正式ChatMessage时仍不认交付 |
| 摘要 | 实际摘要中间件强制触发，两次假模型输入都不含未交付报告 |
| 审批与历史 | 实际HIL中断→本人明确拒绝→未执行副作用；恢复答复用新任务归属。旧检查点导入不发布带标签的生成内容 |
| 故障 | 归属缺失、跨会话、活动旧任务、读取故障都在回答模型之前停止，合成异常密钥标记不出现在返回错误中 |
| 当前相邻集合 | 152通过、3项数据库检查未选、0跳过；Ruff通过。14项新交付检查，其余为既有相邻检查 |

主命令：

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest tests/test_task_delivery.py tests/test_task_cancellation.py tests/test_report_lifecycle.py tests/test_demo.py tests/test_config.py tests/test_search.py tests/test_matching_constraints.py tests/test_career_report.py tests/test_requirements_review.py tests/test_question_context.py tests/test_interviews.py -k 'not direct_report_is_saved and not matching_memory_date and not recent_search_excludes_updates' --tb=short -W error::pytest.PytestUnhandledThreadExceptionWarning --junitxml=data/probe/task-delivery-regression-20261024.xml
.venv\Scripts\python.exe -X utf8 -m ruff check src/polaris/agent/task_delivery.py src/polaris/agent/main.py src/polaris/agent/tasks.py src/polaris/agent/transcript.py tests/test_task_delivery.py tests/test_task_cancellation.py
```

首次组合真实chat时，SQLite默认每线程不同内存连接导致记忆活动回调报“无表”；初次11通过但有2个未处理线程告警，不作为干净通过。夹具改为StaticPool共享内存，两个真实chat例使用memory_first等待回调结束，最终将未处理线程告警视为错误，152项无该告警。没有以SQLite证明生产并发事务安全。证据存data/probe，不含真实用户资料；UI本轮未改、没有新浏览器或真实模型验证。

## 尚欠与下一步

本机制覆盖更新后带归属的网页队列运行。没有擅自给旧版无标签消息／摘要推断任务归属，也没有迁移或修改真实检查点；旧版无标签与直接非队列入口不因此宣称已覆盖。下一步先核实停止时部分并行工具结果缺失的协议完整性；已有_close_dangling只处理最后一条为AI的情况，相关风险还需实际图重现，不当作已修。

真实PostgreSQL读取／锁／检查点、网页停止和刷新、审批批准路径、旧无标签上下文以及独立合成演示仍需验收。当前候选未加载；152项不替代完整求职任务、干净启动、两分钟视频或第一阶段12项审计。

00:25核实Polaris常驻采集Disabled／Enabled=false；5432、55432、8000、8010、3000、3010、8001、3001、9222、9223无监听。本轮无真实模型调用、不改.env／网络、不启动项目后台，原有dirty资料保留。
