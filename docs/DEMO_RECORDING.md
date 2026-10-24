# 两分钟演示录制脚本（待执行）

2026-10-24，面向独立合成实例。**尚未录制视频，没有可用视频链接**；本文件不是已有运行的转写，不预设模型会按脚本成功。先通过[复验执行单](ACCEPTANCE_RUNBOOK.md)和[DEMO启动步骤](DEMO.md)，再按实际结果取镜头。

## 录制条件

只用demo-fixture-v2、合成档案和独立DB／API／网页。模型按实际可用服务配置，口令／key／真实Cookie／私人聊天不进入画面。公司名是示例标签，与真实招聘无关。主任务输入保持DEMO第1条原话，不改成更容易的题来掩盖失败。

若等待影响两分钟时长，明确标注等待已剪辑，并保留完整运行轨迹；不把剪辑后的时长称作端到端延迟。若Token Factory／NVIDIA模型尚未实际运行，不剪入模板字符串当使用证明；这是另一个待验证的提交要求。

## 分镜与英文旁白草案

| 时间 | 必须来自实际产品的画面 | 英文旁白草案／核对点 |
|---|---|---|
| 00:00–00:12 | 合成横幅、当前页面；不要显示个人浏览器标签 | “Polaris is a local workbench for internship search. All jobs, interview material and the profile in this recording are synthetic.” |
| 00:12–00:32 | 输入完整上海Agent任务，显示实际工具活动；有剪辑则标注 | “I can use a temporary city and attendance limit without changing the recorded profile. The task must return evidence, requirements and preparation notes.” |
| 00:32–01:02 | 实际最终报告、岗位“查看详情”及“查看采集原文” | “The report separates source facts from unknowns. Recorded dates do not prove a role is still hiring. An unrecorded internship duration stays unknown.” 若实际第一岗非多城市，不声称它有多城市 |
| 01:02–01:25 | 实际面经原题出处与JD建议，原帖标题／题目范围可见 | “The interview question has stored evidence. A suggested practice question and answer points are preparation advice, not claims about my experience or the interview author's answer.” |
| 01:25–01:44 | 操作者明确“收藏岗位”，刷新后同岗仍收藏，另一岗状态不变 | “Follow-up belongs to this particular job. Saving a bookmark does not mark it as applied.” |
| 01:44–01:55 | 一个真实执行的合成故障例或停止后的可见状态，不能把脚本异常截图当网页结果 | “A missing item or stopped answer is shown honestly. Work already performed and its usage are not silently erased or replayed.” 若实际未验证停止，只讲实际发生的不存在岗位错误 |
| 01:55–02:00 | 当前实际版本和文档入口，状态说明 | “The repository records the evidence and remaining limitations. Cloud monitoring is a later deployment decision.” |

## 发布前要补的材料

- 实际视频文件、英文字幕和对应合成运行记录；录制用代码版本、数据导入时刻、模型／配置、全部调用和用量。费用未知写未知。
- 成功路径或失败路径按事实命名；出现关键误判则先修复复验，不能用手工改答案覆盖屏幕。
- 核实比赛当时的正式规则、提交语言、时长、托管、模型与平台要求，再决定提交版本。当前英文README只是如实说明，不是参赛资格或上线证明。
- 当前不存在云端演示地址，不发布个人实例或沙盒副本，不把录像脚本当作第一阶段第12项的视频交付。
