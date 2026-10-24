# 当前进度

只写现状。过程写 DEVLOG；10-24 之前的长篇状态在 git `638b624` 里。

## 目标（2026-10-24 纠偏）

整链在本机真实跑通 + 采集稳定持续运行 → 补齐功能 → 整体测试。暂不换比赛模型、不推 GitHub、不录视频、不上云。详见 PURPOSE“现在的目标”。

## 现状

- **代码**：后端约 2 万行 Python（采集、抽取、对齐、混合检索、记忆、LangGraph Agent、任务队列、报告），网页 Next.js（对话、岗位、面经、记忆、图谱、追踪、评测、手机配对）。后端全量约 750 项测试通过。
- **整链（10-24 晚实测）**：数据库、API、网页、采集都在本机跑着。网页发“北京 LLM/Agent 日常实习、每周 4 天、3 个月，列岗位并用真实面经准备面试”，77 秒、6 次工具完成：3 个岗位附原文链接和发布时间，天数没写的标待确认，面经题来自真实帖子带出处。过程中修了 4 个真实挡路问题，见 DEVLOG。
- **模型**：`.env` 里的中转服务 10-22 抽取报 403，实际运行用百炼（采集 `qwen-plus`，网页 `qwen3.7-plus`），由 `scripts/run_daemon_bailian.py`、`scripts/run_web_bailian.py` 只在子进程里覆盖。
- **网络**：本机“alijiasu”加速器（TUN + 假 IP）会把百炼流量也拉进隧道，很不稳。`llm/direct.py` 只让百炼域名直连，其他流量不变。

## 采集（10-24 晚）

| 来源 | 情况 |
|---|---|
| 脉脉 | 正常，一轮新写入 86 条，抽取 0 失败 |
| 小红书 | 在跑，偶尔搜索超时，下轮重试 |
| 官网：腾讯 | 19:18 一次 TLS 握手超时，1 小时冷却后重试 |
| 官网：字节、小红书招聘 | 历史上时好时坏（页面超时），待观察 |
| Boss | 不可用：令牌被平台拒绝，每轮只采到 1~2 条就停。需要本人在 Boss 专用窗口核实登录或完成验证 |

## 怎么起

10-24 实际用的起法（`start.ps1` 起的 API 用 `.env` 模型，目前不可用）：

```powershell
docker compose up -d db neo4j
.venv\Scripts\python.exe -X utf8 scripts\run_web_bailian.py --port 8000   # API，百炼模型
npm --prefix web run dev -- -H 127.0.0.1 --port 3000                      # 网页
uv run polaris browser open                                               # 两个采集专用窗口
.\scripts\run_daemon.ps1 -Bailian                                          # 采集常驻，日志 data\logs\daemon.log
uv run polaris status                                                     # 看采集状态
```

开机自启（计划任务“Polaris 常驻采集”）目前是 Disabled，是否恢复待用户决定。

## 下一步

1. Boss 登录：等用户在 Boss 专用窗口处理。
2. 观察采集一晚，看官网批次和小红书是否稳定。
3. 继续按 LOCAL_ACCEPTANCE 在网页上跑其他任务（换城市、零结果、跟进保存与刷新、停止与重连），挡路的问题当场修。
