# 独立演示：代理与后端的数据集模式检查

2026-10-24 01:02（北京时间），承接ac7ac42。第一阶段未通过，第二阶段未部署；本轮未启动项目服务或发送真实HTTP请求。

## 问题与实际范围

原网页只要求配置POLARIS_API，随后向该URL的/api/me路径加OWNER_TOKEN转发。隔离执行实际Next代理：后端即使报告personal模式，POST仍被转发并返回200，正文与合成口令都进入了错误目标的fetch参数。此处fetch完全替换为记录器，未接触真实个人服务；没有声称发生真实资料泄露。

首批7项新代理契约检查修前全部未满足，包含模式检查、逐请求核实、期望数据集头、URL约束和尾斜线处理；这是7项行为，不是7起真实事故。后端鉴权检查为实现后新增，无伪造修前执行证据。

## 修改及接口契约

1. DEMO_MODE=true的网页仅接受127.0.0.1、localhost、[::1]的HTTP(S)根地址，不带账号、路径、query或fragment；末尾斜线归一。个人模式既有API地址规则保留。这里限定本机演示，未扩成云端或容器网页部署方案。
2. 每次演示业务请求先GET公开/health，cache=no-store、credentials=omit、redirect=error，设置AbortSignal.timeout(3000)。不带OWNER_TOKEN／Cookie／业务正文，且在读POST正文前要求HTTP成功、status严格ok、database严格true、config.dataset严格synthetic_demo。无法确认则固定503，不回显原始网络错误，不重试。
3. 实际业务请求加X-Polaris-Expected-Dataset：演示synthetic_demo、个人personal。演示业务fetch不跟重定向；流式响应、方法、查询、正文和后端错误状态继续转发。个人模式不增加health请求。
4. 后端实际require_owner在鉴权与业务执行前核对该可选头；当前模式不符返回409且不执行操作。它用于防误连／模式变更，不能替代口令鉴权。没有该头的旧客户端仍使用原口令规则，没有新HTTP端点或新依赖。
5. 健康检查与转发并非一个原子步骤；若两者之间当前API模式变了，新头由当前版本API继续核对。它不承诺向错误进程绝不传输任何字节，不是对恶意伪造health服务的身份认证；需要网页与API都加载本次代码，未验证旧服务具备此约束。

一次演示代理操作增加一次公开health请求及对应后端DB健康探测，无模型调用。尚未测真实延迟／并发开销，不把本机模块耗时当产品SLA。

## 验证

| 范围 | 结果及限制 |
|---|---|
| 实际Next配置／route模块 | TypeScript转译后隔离执行，全部fetch替换；模式不符时未读正文、不附口令、无业务转发；合法模式才转发 |
| 健康与网络故障 | 覆盖空／缺字段、degraded、数据库false或字符串true、未知dataset、非JSON、503、307与网络异常；固定503，不回显合成异常／口令 |
| 连续请求 | 第一次合法，第二次变personal时重新核实并阻止；不缓存前次成功 |
| 正常和兼容 | SSE正文及Content-Type保留，POST方法／query／body保持；个人模式仅一次业务请求；缺口令不发health或业务请求；409与连接故障不自动重试 |
| 当前真实鉴权依赖 | 10项有限ASGI内存请求／直接依赖调用，检查demo／personal匹配与不匹配、未知期望、旧无头、错误口令、未配口令、先前health不作当前证明；拒绝时业务动作0 |
| 当前相邻集合 | 后端182通过、3项数据库检查未选、0跳过；网页配置8＋代理9共17通过；TypeScript／ESLint／Ruff退出0 |

ASGI应用仅是挂载实际require_owner的最小内存测试，不加载Polaris lifespan、不启动任务服务或监听端口。Node测试捕获请求选项与返回体，不验证浏览器、真实HTTP超时／重定向、真实服务重启或PG连接。

证据在data/probe/demo-api-identity-before-20261024.tap（首批7项修前失败）、demo-api-identity-after-20261024.tap（当前网页17项通过）、demo-api-identity-static-20261024.json（各检查退出码）、expected-dataset-after-20261024.xml及demo-api-identity-regression-20261024.xml。

主命令为web目录node --test tests/test_demo_proxy.mjs tests/test_demo_config.mjs、node_modules/.bin/tsc.cmd --noEmit及eslint.cmd src/lib/server.ts 'src/app/api/[...path]/route.ts'；后端在原172项命令前加tests/test_expected_dataset.py，保留三项数据库未选与线程告警按错误处理。Ruff检查src/polaris/api/personal.py和tests/test_expected_dataset.py。

## 运行与下一步

01:01核实Polaris计划任务Disabled／Enabled=false，5432、55432、8000、8010、3000、3010、8001、3001、9222、9223无监听。未拉镜像、启动服务、访问个人API、改.env／网络或调用真实模型；候选未加载。原六份dirty文档及10-22三份未跟踪资料保留。

下一步对照LOCAL_ACCEPTANCE逐项梳理独立演示交付，明确可离线完成的材料与仍需实际运行的证据，不继续用新增模块通过数代替整条求职任务。真实镜像／PG迁移与导入／网页完整任务／重复与预留／独立启动／两分钟视频仍欠，本阶段不能宣布完成。
