# 独立演示：配置与迁移离线核查

2026-10-24 00:49（北京时间），承接ed7888b。第一阶段未通过、第二阶段未部署；候选未加载，本轮无服务启动或真实模型调用。

## 可复现失败

实际Alembic入口优先返回ini／DATABASE_URL，绕过get_settings的演示校验与显式文件缺失检查。已选DEMO_MODE=true文件，继承个人DATABASE_URL时，离线命令仍生成SQL；在线入口能走到被测试阻止的create_engine。没有连接或修改个人库。选中仅数据库字段的演示配置时，又因完整Settings要求模型字段而无法离线迁移。

网页server.ts捕获文件读取异常后直接当作未配置：显式演示文件不存在／空路径时可能按继承值或个人默认API继续。非法DEMO_MODE也被当成false。Python原utf-8读取带BOM文件时，第一项DEMO_MODE可能不被识别，与网页行为不一致。

修前实际Alembic9项8失败1通过；网页实际模块8项3失败5通过。它们是配置解析／命令入口复现，不是已发生的真实个人库迁移或网页数据泄露。

## 修改

1. 应用与迁移共用显式配置文件选择和演示URL限制。指定文件缺失／空路径先拒绝，不用其他URL跳过。应用和迁移支持UTF-8 BOM。
2. 迁移读取数据库字段与DEMO_MODE，保留ini覆盖→环境→文件→默认的URL优先级；在DEMO_MODE为真时检查最终目标，拒绝个人库／远程库。没有把模型可用性设为迁移前提。
3. 网页显式配置文件不可读／空路径直接停止，即使继承了个人OWNER_TOKEN和POLARIS_API；非法DEMO_MODE直接报错。未显式选文件的纯环境变量个人配置仍可用，合法环境变量覆盖规则保留。配置在模块加载时读取，编辑后需重启该进程。
4. DEMO.md增加不连接DB的应用／迁移配置一致性预检，纠正“演示缺API会回8000”的旧文字。DEMO_MODE仍受环境变量优先级影响，操作者必须核对为true；不按文件名猜测模式，不擅自清用户环境变量。

无新依赖、表或HTTP接口，没有编辑个人.env／创建真实.env.demo。依据为当前Pydantic Settings实际读取行为、现有Alembic env.py与实际离线命令输出；没有以假模型替代真实启动验收。

## 验证证据

| 范围 | 当前证据与限制 |
|---|---|
| 实际迁移入口 | 个人环境URL／ini覆盖在online与offline均被拒绝；构造引擎之前拦截。缺失／空显式路径不被DATABASE_URL跳过 |
| 14个迁移离线渲染 | 不配置模型字段也可从base到head c41d7e9a2b10，所有14个revision出现在SQL中，包含vector／pg_search／BM25 jieba／聊天与跟进表，COMMIT结束；不证明PG语法、扩展或执行成功 |
| 模式／兼容 | 个人模式原ini优先级保留；应用与迁移的BOM第一项DEMO_MODE都识别并保留目标限制 |
| 后端相邻集合 | 172通过、3项数据库检查未选、0跳过，Ruff通过；其中10项新迁移／配置检查，不含真实PG连接 |
| 网页模块与静态 | 实际server.ts转译后在隔离文件／环境边界执行，8通过；Node／TypeScript／ESLint退出码分别0，没启动网页或浏览器 |

实际离线SQL19471字节，SHA-256为400ff8379e21e5a127ff2cfa3611b7cb3f3e527cf481a86585bf7bd97d070bc6。保存data/probe/demo-schema-offline-20261024.sql及同名JSON，使用临时纯合成配置，create_engine明确被阻止。不要将这份SQL的存在当成扩展初始化成功或自动执行许可。

主要命令：

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest tests/test_demo_migrations.py tests/test_stopped_tool_protocol.py tests/test_task_delivery.py tests/test_task_cancellation.py tests/test_report_lifecycle.py tests/test_demo.py tests/test_config.py tests/test_search.py tests/test_matching_constraints.py tests/test_career_report.py tests/test_requirements_review.py tests/test_question_context.py tests/test_interviews.py -k 'not direct_report_is_saved and not matching_memory_date and not recent_search_excludes_updates' --tb=short -W error::pytest.PytestUnhandledThreadExceptionWarning --junitxml=data/probe/demo-config-and-migrations-regression-20261024.xml
```

网页在web目录执行node --test tests/test_demo_config.mjs、node_modules/.bin/tsc.cmd --noEmit及eslint.cmd src/lib/server.ts。私有记录demo-migrations-before-20261024.xml、demo-migrations-after-20261024.xml、demo-config-and-migrations-regression-20261024.xml、demo-config-web-after-20261024.json；修前网页结果保留在本轮工具输出与本表，不伪称已导出修前JSON。

## 尚欠与下一步

00:49核实Polaris计划任务Disabled／Enabled=false；5432、55432、8000、8010、3000、3010、8001、3001、9222、9223无监听。没有启动Docker容器／数据库／API／网页／专用浏览器，也未拉镜像、导入或做模型请求。

下一小步核对演示API的身份与前端代理边界：显式POLARIS_API不等于已经确认目标dataset，当前仍靠手动健康核对；先准备可离线验证的故障行为，不自行发请求到个人服务或启动服务。真正干净环境迁移／导入／重复导入、关键词命中、网页整任务和视频仍欠，第一阶段第11／12项不因此记通过。
