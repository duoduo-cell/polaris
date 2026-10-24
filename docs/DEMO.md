# 合成演示

给 HR、评委看的独立实例：岗位、面经、个人档案全部是手写的虚构资料（公司名“腾讯”只是示例标签），不含真实采集原文、登录态或个人信息。和作者自用的实例完全分开：独立数据库 `polaris_demo`、独立 API（8010）、独立网页（3010）。

2026-10-24 在本机按下面的步骤从空库实际跑通，网页上的演示任务结果见文末。之前长篇的离线检查记录在 git `638b624` 之前的版本里。

## 启动（项目根目录，PowerShell）

需要 Docker、uv、Node.js，以及一个 OpenAI 兼容的模型服务。

1. 准备配置：复制 `config/demo.env.example` 为 `.env.demo`，填独立的数据库密码和 OWNER_TOKEN（随机串即可）。`.env.demo` 不进仓库。
   - 模型密钥可以直接写在 `.env.demo` 的 `LLM_API_KEY`；本机作者用 `scripts\run_demo_bailian.py` 在进程里借用个人配置的百炼密钥，不写进文件。
2. 起数据库、建表、导入合成资料（只往空的 `polaris_demo` 导入，非空库会拒绝）：

```powershell
$env:POLARIS_ENV_FILE = (Resolve-Path .env.demo).Path
docker compose --env-file .env.demo -f compose.demo.yml up -d --wait db
uv run alembic upgrade head
uv run python -m polaris.demo --seed --apply
```

3. 起 API：

```powershell
.venv\Scripts\python.exe -X utf8 scripts\run_demo_bailian.py --port 8010   # 或：uv run polaris serve --port 8010
```

4. 起网页（单独构建到 `web/.next-demo`，不影响个人网页的 `.next`）：

```powershell
$env:POLARIS_ENV_FILE = (Resolve-Path .env.demo).Path
$env:POLARIS_API = "http://127.0.0.1:8010"
$env:POLARIS_NEXT_DIST = ".next-demo"
npm --prefix web run build
npm --prefix web run start -- -H 127.0.0.1 -p 3010
```

打开 http://127.0.0.1:3010 ，顶部应有“合成演示”黄条；`http://127.0.0.1:8010/health` 的 `config.dataset` 应为 `synthetic_demo`。

停止：关掉 API 和网页进程，`docker compose --env-file .env.demo -f compose.demo.yml stop`（保留数据卷）。

## 安全边界

- 演示 API 只查合成标记的资料，只用关键词召回（不跑向量、图谱、重排和平台现查），关闭外部导入和 MCP，不读本机采集记录、简报和推送设置。
- 网页每次转发前先查演示 API 的 health，确认是 `synthetic_demo` 才转发，否则 503；不会误连个人 API。
- `DEMO_MODE=true` 时，迁移和导入都拒绝指向个人库。

## 预置资料

5 个岗位、1 篇面经、1 道有原句的题、8 条虚构个人记忆：长期想去北京、本科在读、无论文、每周最多 4 天，**没有记录可实习月数**。

## 演示任务（2026-10-24 实测，模型 qwen3.7-plus）

| 任务 | 结果 |
|---|---|
| “在合成演示库找上海Agent日常实习，每周最多4天，最多列2岗。展开第一岗学历、论文、出勤和实习月数与我的情况，解释日期。再给同公司一道面经原题和一道JD自测题……” | 23 秒、3 次工具。第一岗学历、论文两项硬冲突标出，实习月数标待确认；发布／保存／最近识别三个日期分开解释；面经原题来自合成帖子，JD 自测题引用原文 |
| 岗位详情里标“已投递” → 起草跟进消息 | 草稿用占位填卡片里没有的实习时长和到岗时间，不会写“满足要求”；北极星不发送 |
| 标“未通过”并写理由 | 理由记进卡片“被拒的反馈”；“投递”页漏斗、渠道、每周投递量随之更新；刷新后仍在 |
| “我上次被拒的原因是什么？下次该先补什么？” | 引用被拒反馈，给出针对性的补强点 |
| “上海RAG实习每周最多3天有没有？” | 唯一候选没写天数：保留并标待确认，不说成符合 |
| 回答中途点停止，再问“北京RAG实习每周最多3天” | 显示“你停止了这次回答”；下一问按北京重新查，方向按 RAG 算 |
| “查看不存在的岗位J-999999的详情” | 如实说库里没有，不编造 |

已知局限：查询条件里的“每周最多 N 天”有时会被记忆模块当成你自己的到岗天数（会先问你确认，或在你重复说时更新）。
