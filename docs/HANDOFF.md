# 接续入口

10-24 用户纠偏后重写。之前的长篇交接（编号 `polaris-research-20261021` 及 10-23／10-24 的逐轮接续）在 git `638b624` 里，需要时用 `git show 638b624:docs/HANDOFF.md` 查。

## 接手先做

1. 读 `AGENTS.md` 顶部“当前目标”、`docs/PURPOSE.md`“现在的目标（10-24 纠偏）”、`docs/STATUS.md`。
2. 核实运行状态：`docker ps`、8000／3000／9222／9223 端口、`data/logs/daemon.log` 末尾。旧 PID 不可沿用。Docker Desktop 装在 `D:\Docker\Docker\Docker Desktop.exe`，`up.ps1` 不会自己打开它。
3. 按 STATUS 和 [v0.2.0 Milestone](https://github.com/duoduo-cell/polaris/milestone/1) 继续。

## 仓库和提交流程（10-25 起）

- 仓库 [duoduo-cell/polaris](https://github.com/duoduo-cell/polaris) 已公开，`main` 受保护：不能直接推，对管理员也生效。`AGENTS.md` 里“做本地提交、暂时不推到远端”那条已经过时，以这里为准。
- 每个改动：先开 Issue，从最新 `main` 拉分支（`feat/`、`fix/`、`docs/`、`ci/`），提 PR，按模板写为什么、改了什么、前后证据、没覆盖的。两项 CI 都通过后 squash 合并，合并后分支自动删除。
- `gh` 登了两个账号：`duoduo-cell` 是当前活动账号，`2063365572abc-bot` 不用。绝不能用另一个账号去 star、fork 或开 Issue，那是虚假互动。
- 版本记在 [CHANGELOG](../CHANGELOG.md)。v0.1.0 已发布；v0.2.0 对应比赛提交的 Milestone。标签 `owner-action` 表示只有作者本人能做的事。
- 本机采集的原文只在 `data/` 下，7 段含小红书原文的回放录像只在本机跑，所以 CI 是 785 项，本机是 792 项。

## 有效决定

- 主线：整链真实跑通、采集稳定、补齐功能、整体测试。只修真实挡路的问题，不做离线边角打磨。
- 可以启动／重启本机数据库、API、网页、采集和采集浏览器。恢复开机自启、云资源、对外消息先问用户。
- 比赛不接 Nebius Token Factory 和 NVIDIA 模型，用百炼 Qwen 提交并如实写明（10-25 用户决定，见 SHOWCASE 第 4 节）。百炼入口见 STATUS。
- 采集边界不变：本人账号、只读、低频、不伪装、遇验证码停下等用户（见 AGENTS“边界”）。
- 真实原文、登录态、个人信息不进仓库和公开演示。
