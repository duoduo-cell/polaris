# 独立合成演示（候选，尚未完成干净环境与网页验收）

此入口用于不含真实采集原文和个人资料的本机演示。预置岗位、面经和个人档案均为人工虚构，公开公司名称仅为示例标签，与实际招聘无关；使用现有求职工具、记忆、报告与跟进接口，模型仍需使用者自己的联网服务并产生用量。

它与 `polaris sandbox` 不同：后者复制真实数据库，不能用于公开展示。此入口只向**全新且为空的 `polaris_demo`** 导入手写资料，不导出／复制个人库，不读取平台Cookie，不部署云端，不安装计划任务。已有个人 `.env` 不变。

## 当前可验证范围

- 2026-10-24补充：迁移入口与应用共享配置文件及演示目标检查，DEMO_MODE为真时，继承的DATABASE_URL或Alembic显式覆盖都不能指向个人库；指定文件缺失／空路径直接报错。网页也拒绝显式配置不可读／空路径及非法DEMO_MODE，不回退个人API。环境变量仍优先，必须先核对DEMO_MODE未被覆盖为false。详见[迁移与配置证据](research/2026-10-24-demo-config-and-migrations.md)。
- 演示网页只接受本机HTTP(S)根地址（127.0.0.1／localhost／[::1]），不接受账号、路径、查询和片段。每次业务转发前不带口令检查公开health，要求status=ok、database=true、dataset=synthetic_demo；不符／无法读取时503停止转发。业务请求声明期望数据集，当前API模式不符时409且不执行操作；旧无该头客户端仍按原口令鉴权。验证仅有隔离模块／内存ASGI，不是已连真实演示API，见[代理模式检查](research/2026-10-24-demo-api-identity.md)。

- 当前资料版本 `demo-fixture-v2`。第一版手工标注的虚构公司不在现有词表，导致同题重解析归属不同；现在用公开公司名作显式演示标签，题目元数据由现有原文解析生成。没有放宽真实来源规则或复制真实面经。旧v1成功标记不会自动升级或重置；本机此前没有执行过演示导入。
- 默认运行 `uv run python -m polaris.demo` 只在内存构造并检查样本，不读取模型配置、不连接数据库、不启动服务。
- 数据写入需要 `--seed --apply`；实际引擎与配置的目标都必须是本机或独立Compose中的 `polaris_demo`。非空库且没有本演示的成功导入记录时拒绝，不清理或覆盖。
- 已导入同一版本时重复执行不再次写入，不清除之后的聊天或跟进；这条目前仅有离线事务探针证据，真实PostgreSQL导入尚未执行。
- 空库检查同时覆盖业务表与现有LangGraph检查点，不能把“业务表空、模型历史仍有资料”的库当成干净演示库。指定的配置文件缺失时停止，不回退个人配置；网页未指定演示API时也不回退默认个人端口。
- 演示只查询合成标记，并使用关键词召回；不执行向量、图谱、重排和真实平台现查。网页有合成说明；实际渲染待验。
- 演示API启动前检查成功导入记录、合成标记及来源；关闭外部导入和MCP；不读取个人简报与采集日程，反馈写独立 `data/demo/`。这不是允许混入真实聊天后再公开的脱敏工具。

## 手动准备和启动步骤

以下是待实际验收的启动流程，不是已验证的“三分钟一键运行”。当前开发助手未执行这些启动命令。只有使用者明确要启动时执行；没有开机自启、常驻采集和自动重启策略。

需要Docker、Python 3.12+、uv和Node.js。克隆仓库后，在项目根目录用新PowerShell窗口操作：

1. 使用者复制 `config/demo.env.example` 到根目录 `.env.demo`，填写独立数据库密码、独立OWNER_TOKEN和自己的模型服务配置。数据库URL中的密码与DEMO_DB_PASSWORD保持一致；URL含特殊字符时做URL编码。不要提交 `.env.demo`，不要将真实Cookie、简历或采集数据库复制进来。
2. 安装项目依赖并指定**这个配置文件的绝对路径**。环境变量优先于文件；新窗口中不要继承个人DATABASE_URL、DEMO_MODE、POLARIS_API或OWNER_TOKEN的覆盖值。编辑文件后重启对应进程，应用和网页都按该进程加载的配置工作。

```powershell
uv sync --frozen
$env:POLARIS_ENV_FILE = (Resolve-Path .env.demo).Path
uv run python -m polaris.demo
uv run python -c "from polaris.config import get_settings; from polaris.db.migration_config import migration_database_url; s=get_settings(); assert s.demo_mode, 'DEMO_MODE必须为true'; assert migration_database_url()==s.database_url, '迁移与应用目标不一致'; print('演示配置与迁移目标一致；未连接数据库，未校验模型服务。')"
```

3. 明确启动独立数据库，等待健康检查通过。此Compose只定义数据库，绑定127.0.0.1:55432，项目名 `polaris-demo`，使用独立卷，未挂载个人数据。

```powershell
docker compose --env-file .env.demo -f compose.demo.yml up -d --wait db
uv run alembic upgrade head
uv run python -m polaris.demo --seed --apply
```

`POLARIS_ENV_FILE`让Alembic和应用使用演示配置；如果已有 `DATABASE_URL` 环境变量，它优先于文件，应在新窗口中清除或核对。DEMO_MODE为真时，迁移在构造引擎前检查最终目标，不能用命令行／环境变量绕过目标限制。迁移本身只需数据库配置，不要求模型密钥；上面的应用预检会要求配置字段，但不发送模型请求。配置错误应停止，不能改名个人库、清库或将真实副本标为合成来继续。

4. 在该窗口手动启动API（前台运行，Ctrl+C停止）。

```powershell
uv run polaris serve --port 8010
```

5. 新开PowerShell窗口到同一项目根目录，给网页指定相同配置文件与演示API。演示模式没有API配置会报错；不要依赖个人默认8000。示例文件已有POLARIS_API，仍应同时设置并核对。

```powershell
$env:POLARIS_ENV_FILE = (Resolve-Path .env.demo).Path
$env:POLARIS_API = 'http://127.0.0.1:8010'
npm --prefix web ci
npm --prefix web run dev -- -H 127.0.0.1 --port 3010
```

6. 手动打开 `http://127.0.0.1:3010`。先核对“合成演示”说明和健康接口的 `config.dataset=synthetic_demo`。网页还会逐请求核对演示health（设置3秒超时，不自动重试），因此需要先启动并确认演示API健康；不能用只显示横幅证明连接正确。API和网页选择的文件、OWNER_TOKEN及端口必须一致，均需加载当前版本；不要沿用个人网页的浏览器页面或Cookie。此版本没有远程公开部署或托管演示地址。

停止网页和API后，手动停止此独立数据库（保留演示卷，不删除数据）：

```powershell
docker compose --env-file .env.demo -f compose.demo.yml stop
```

## 预置资料与演示任务

预登记场景、记录条件和与12项验收的对应见[复验执行单](ACCEPTANCE_RUNBOOK.md)，原文事实见[合成事实对照](../eval/reports/demo-fixture-facts-20261024.json)。事实对照不是模型答案、检索排序或运行结果；录像按[待执行脚本](DEMO_RECORDING.md)准备。

导入时以当时UTC时刻构造相对日期：虚构发布日期早于快照保存，再早于最近识别。5个岗位、1篇面经、1道有原句的题、12块无向量合成索引、8条虚构个人记忆；个人长期北京、本次可查上海，本科在读、无论文、最多每周4天，**没有记录可实习月数**。样本使用“腾讯”这一公开公司名称作标签，所有岗位与面经均为手写合成，不能当作该公司的真实岗位或题目。演示记录不关联真实大厂目录项，默认不启用大厂筛选；要求只查真实大厂目录时会为空，不伪装成真实覆盖。

建议按同一会话完成并保存实际轨迹：

1. “在合成演示库找上海Agent日常实习，每周最多4天，最多列2岗。展开第一岗学历、论文、出勤和实习月数与我的情况，解释日期。再给同公司一道面经原题和一道JD自测题，各给答题要点，列出待核实项。”第一岗按实际所列核原文，不预设检索排序；工具工程岗原文含多城市，也不能确认各城当前名额。个人可实习月数未知；题目轮次未知但原帖标题含一面与二面；两题来源及建议分开。
2. “把Agent研究实习（合成）按我的条件列出冲突，仅作对照，不当推荐。”预期明确硕士／论文硬冲突，不由方向匹配或高分抵消；使用岗位名称，不猜上一列表的第三条。
3. “上海RAG实习每周最多3天有没有？”预期保留天数未知的解释，不能把未写当符合；不扩城市凑数。再用“北京RAG最多3天”核对新范围，不沿用上海结论。
4. 由演示操作者在岗位详情明确保存收藏或待准备状态，刷新页面核对仍指向同一岗位；不要记录真实投递。重复提交和响应丢失仍按本机验收清单核对。
5. 故障演示候选：“查看不存在的岗位J-999999的详情。”预期工具错误如实说明，不能编造该岗或无限重试。保留实际工具调用、最终回答与刷新状态；不把内部模拟错误当作网页故障恢复已经通过。

这些是待实测预期，不能用手写示例答案或脚本模型代替真实模型完成验收。

## 尚未完成的验收

2026-10-24 01:02补充：当前后端相邻182通过、3项数据库检查未选、0跳过；网页实际配置／代理模块17通过，TS／ESLint／Ruff通过。原代理误连个人模式仍转发已在隔离fetch中复现并修正，后端以有限ASGI请求验证模式拒绝先于业务动作；没有真实HTTP／数据库、API重启或网页验收。

2026-10-24离线配置与迁移核查：当前相邻后端172通过、3项数据库检查未选、0跳过；网页实际配置模块8项通过，TypeScript／ESLint／Ruff通过。14个迁移成功渲染离线SQL至head c41d7e9a2b10，包含vector／pg_search初始化及jieba BM25索引；没有构造数据库引擎或由PostgreSQL解析／执行，不证明镜像扩展可用。真实迁移、导入、网页和模型验收仍欠。

2026-10-23离线验证：后端与相邻集合110项通过、3项数据库检查未选、0跳过；4项网页配置模块检查通过，TypeScript、ESLint、Ruff通过；`docker compose ... config --quiet`只做配置解析并退出0，没有拉取镜像或启动容器。私有记录为 `data/probe/demo-offline-regression-20261023.xml`、`demo-web-static-20261023.json`、`demo-compose-config-20261023.json`。初版新增检查中1项把既有实习时长解释的key误写成duration_months而失败，改为实际months后通过，这是测试夹具错误，不计产品修复。

v2后续检查（同日）：125项通过、3项数据库检查未选、0跳过，包含8项既有面经解析检查和4项真实LangGraph／内存checkpointer生命周期检查；Ruff、TypeScript、ESLint通过。题目／帖子同源归属和报告独立合成说明两项修改前失败，修改后通过。完整报告和模型失败时的部分报告都保留合成说明，复制文字不会仅依赖网页横幅。另补报告已生成但最后会话写入／读回失败的“保存未确认”处理；这不代表真实PostgreSQL断连或网页重连已验收。证据 `data/probe/report-lifecycle-and-demo-after-20261023.xml`。

独立镜像拉取、扩展初始化、迁移、实际导入／重复导入、真实关键词命中、主模型选择报告工具、合成网页展示、跟进／刷新与停止故障，均未在此入口执行。现有离线检查覆盖配置隔离、SQL过滤、工具边界、样本和调用顺序，不能代替这些要求。第一阶段仍未通过。

演示使用[ParadeDB v0.22.6官方Dockerfile](https://github.com/paradedb/paradedb/blob/v0.22.6/docker/Dockerfile)对应的官方镜像标签，其默认PostgreSQL主版本是18，包含pg_search和pgvector；这与个人现有PG17卷分离。没有复用本机未提交的扩展安装包，未拉取镜像或验证本机能否下载；当前只是候选依赖，不能宣称干净环境已通过。使用者的模型服务可用性、成本和支持参数仍需小请求与真实任务核查。
