# 停止请求与迟到结果的收尾一致性

2026-10-24 00:09（北京时间）。第一阶段仍未通过；本轮只改任务队列的停止／收尾机制，不启动北极星服务。基础代码为5884897／main-v36，未改变模型提示或配置。

## 失败及定位

用户停止任务时，执行线程的stopper每0.5秒才读一次标记。最后一次读到false后，模型／报告工具可能已经返回；旧_work直接finish_turn并写completed，没有最终查标记。停止、认领和活动回调也可能在等会话锁前拿到ORM旧状态。

以当前13项确定性时序检查运行旧版tasks.py：11失败、2通过。失败包括迟到报告被存成completed、等锁期间取消的queued任务被重新认领、停止按旧queued状态覆盖running／completed，以及结束后迟到草稿重新写入live。普通成功与未请求停止的普通故障在旧版也通过，不算新增改进。

这是离线注入，不是声称已捕获真实PostgreSQL并发事故。检查使用SQLite内存中的三个聊天表投影、实际SQLAlchemy身份映射和实际finish_turn；在加载后或取得锁的边界直接改变存储状态，保留旧对象以检查重新读取。没有仿真PG advisory锁、多连接隔离、生产迁移或网页。

## 修改

1. cancel、claim取得已有会话事务锁后刷新状态；取消不覆盖已完成任务，认领跳过已取消的队列项。
2. 最终交付在同一会话事务锁内读取任务，直接检查最新cancel_requested，不依赖轮询缓存。停止先提交时撤下未交付的answer与results，保存停止状态；完成先提交时保留已经正式交付的答案。
3. 保留已发生的工具记录、记忆事件、模型调用ID及费用未知值。停止不回滚已生效操作，不把已发生的调用记作0。
4. 停止立即清空live；活动回调取得锁并刷新，结束任务不再接收迟到活动，已请求停止时不再收草稿／压缩预览。仍在运行时返回的真实工具活动可以留档。
5. 异常收尾也按最新状态处理：已请求停止的记cancelled；普通异常仍为failed，不能让迟到异常覆盖已有终态。
6. 返回结果若含已挂起审批，保留waiting_approval及动作，由用户批准／拒绝；停止不替用户做决定。网页新增“回答已停止，待确认动作仍需你批准或拒绝”的说明，尚未做实际页面验收。

## 证据

| 范围 | 结果 | 限制 |
|---|---|---|
| 同一13项检查、旧5884897任务代码 | 11失败、2通过 | 仅在独立Python进程内替换被测模块，不改工作区源码；2条pytest已导入插件告警 |
| 当前任务机制13项＋既有报告生命周期4项 | 17通过 | 既有4项使用实际LangGraph和内存checkpointer |
| 相邻整体集合 | 138通过、3项数据库检查未选、0跳过 | 不含当前PG／网页或真实模型调用 |
| Ruff／TypeScript／定向ESLint | 全部退出0 | 没有浏览器渲染或操作证据 |
| 运行边界00:08 | Polaris常驻采集Disabled／Enabled=false；5432、55432、8000、8010、3000、3010、8001、3001、9222、9223无监听 | 有限端口核实，不宣称所有Windows自启项都已关闭 |

主要命令：

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest tests/test_task_cancellation.py tests/test_report_lifecycle.py tests/test_demo.py tests/test_config.py tests/test_search.py tests/test_matching_constraints.py tests/test_career_report.py tests/test_requirements_review.py tests/test_question_context.py tests/test_interviews.py -k 'not direct_report_is_saved and not matching_memory_date and not recent_search_excludes_updates' --tb=short --junitxml=data/probe/task-cancellation-regression-20261024.xml
.venv\Scripts\python.exe -X utf8 -m ruff check src/polaris/agent/tasks.py tests/test_task_cancellation.py
```

前端命令在web目录：node_modules/.bin/tsc.cmd --noEmit；node_modules/.bin/eslint.cmd src/app/chat/page.tsx。

私有证据留在data/probe：task-cancellation-baseline-20261024.py及同名XML；task-cancellation-after-20261024.xml；task-cancellation-regression-20261024.xml。首次搭建SQLite投影时误删created_at默认值而发生夹具错误，修正后才进行旧版对照；初始错误不计产品缺陷或通过证据。

## 尚欠及下一步

- 本轮保护任务结果和正式聊天记录，不会删除LangGraph已经写入的AI／工具消息。停止可能发生在检查点写入之后，因此未向用户交付的报告仍可能留在下一轮模型上下文。这一层尚未修复或验证，不能宣称“停止完全一致”。下一小步先在实际图＋内存checkpointer复现下一轮上下文问题，再决定如何标记／排除未交付内容，不靠删除历史或自动重跑模型。
- PostgreSQL真实事务顺序、网页停止／刷新及审批恢复需在允许运行时另验；目前候选未加载到服务。
- 部分工具或HTTP请求已经发出时，停止不保证立即终止远端执行或计费；不承诺回滚。
- 第一阶段12项验收、独立干净启动、完整任务重复／预留请求、两分钟演示仍欠。第二阶段持续云端Agent未部署，云设施／预算／运维安排未定。

当前保持禁用自启，没有启动采集、专用浏览器、数据库、API或网页，没有模型调用、改.env或网络配置。既有其他dirty文档与10-22三份未跟踪调研资料保留。
