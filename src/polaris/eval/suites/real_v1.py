"""工具调用评测套件 real-v1：真实数据（大厂官网岗位 + 小红书笔记），工具换成 jobs_* / posts_*。

和 v0 一样按最终结果评分（τ-bench 的思路）：回答里有没有对的值、有没有写出依据编号、该不该调工具。
比 v0 难在：公司要从简称、英文名、黑话归一（TikTok、pdd），要在岗位和笔记两类工具之间选，
有三条内容几乎一样的搬运帖要认出来，还有库里确实没有的要如实说没有。
题目里的编号在建题时按来源编号去库里查，重新抽取后编号变了也能对上。
"""
from sqlalchemy import select

from polaris.db.models import Job, Post, Posting, RawItem
from polaris.db.session import session_scope
from polaris.eval.suites.tool_calling import Task

SUITE_VERSION = "real-v1"
NOT_FOUND = ["没有", "没找到", "未找到", "找不到", "暂无", "没查到", "未查到", "不存在"]
DAYS_4 = ["4天", "四天", "每周4", "至少4", "4 天", "≥4"]

# 题目依据的条目：(来源, 来源里的编号)
REFS = {
    "tx_coding": [("tencent:careers", "2108502775083216896"), ("tencent:careers", "2104878897891033088")],
    "bd_doubao": [("bytedance:campus", "7677980451091171589")],
    "bd_seed": [("bytedance:campus", "7646379615653677365")],
    "xhs_cs": [("xhs:careers", "22481")],
    "tiktok": [("xhs", "6aca6c8b0000000002013860")],
    "pdd": [("xhs", "6ac74611000000001a032d3a")],
    "didi": [("xhs", "6acaf8d40000000018005549")],
    "mihoyo": [("xhs", "6acaf660000000001a02ac40"), ("xhs", "6acae6d200000000150023eb"),
               ("xhs", "6acae2ad0000000018013c09")],
}


def _short_ids() -> dict[str, list[str]]:
    with session_scope() as s:
        # M4 起 J- 指合并后的岗位：引用的挂牌换成它所属的岗位
        jobs = {(src, ext): f"J-{jid}" for src, ext, jid in s.execute(
            select(RawItem.source, RawItem.external_id, Posting.job_id).join(Posting, Posting.raw_item_id == RawItem.id)
            .where(Posting.job_id.is_not(None)))}
        posts = {(src, ext): f"P-{pid}" for src, ext, pid in s.execute(
            select(RawItem.source, RawItem.external_id, Post.id).join(Post, Post.raw_item_id == RawItem.id))}
        # 字节在杭州的 Agent 实习：符合条件的都算对，随采集增加
        hz = [f"J-{i}" for i in s.execute(select(Job.id).where(
            Job.company == "字节跳动", Job.city.ilike("%杭州%"), Job.title.ilike("%agent%"))).scalars()]
    both = jobs | posts
    return {"bd_hz": hz} | {name: [both[r] for r in refs if r in both] for name, refs in REFS.items()}


def build_tasks() -> list[Task]:
    ids = _short_ids()
    q_tx = "腾讯在新加坡有没有 Coding Agent 方向的实习？请写出依据的编号。"
    q_didi = "滴滴的面经里，二面问了哪些问题？请写出依据的编号。"
    tasks = [
        Task("bd-hangzhou", "multi", "字节在杭州有哪些 Agent 方向的日常实习？列几个岗位名，写出编号。",
             [["泛抖音", "App Infra", "开发者服务", "数据平台", "Agent Harness", "中国交易与广告", "创作Agent",
               "抖音用户产品", "评测实习生"]], ids["bd_hz"]),
        Task("tx-singapore", "lookup", q_tx, [["Coding Agent Research Intern"]], ids["tx_coding"]),
        Task("bd-doubao-time", "lookup", "字节的「豆包大模型Agent算法实习生 - Data AML」每周要到岗几天、至少实习多久？请写出依据的编号。",
             [DAYS_4, ["6个月", "六个月", "6 个月", "半年"]], ids["bd_doubao"]),
        Task("xhs-kefu", "multi", "小红书校招里有没有做智能客服 Agent 开发的岗位？base 在哪个城市？请写出依据的编号。",
             [["智能客服"], ["上海"]], ids["xhs_cs"]),
        Task("en-seed", "english", "Does ByteDance have an Agent R&D intern role on the Seed team? Which city is it in? "
             "Cite the job id.", [["Seed"], ["上海", "Shanghai"]], ids["bd_seed"]),
        Task("alias-tiktok", "alias", "TikTok 的 Agent 岗有人发过面经吗？大概问了什么？请写出依据的编号。",
             [["取数", "SQL", "schema", "Schema"]], ids["tiktok"]),
        Task("alias-pdd", "alias", "pdd 的 agent 开发一面问了哪些问题？请写出依据的编号。",
             [["agent loop", "agentloop", "Agent loop", "Agent Loop", "工具调用", "范式", "停止"]], ids["pdd"]),
        Task("didi-round2", "posts", q_didi,
             [["上下文压缩", "workflow", "Workflow", "langgraph", "LangGraph", "benchmark", "Benchmark", "最大子数组"]],
             ids["didi"]),
        Task("mihoyo-pay", "posts", "米哈游实习的日薪大概多少？每周要到岗几天？请写出依据的编号。",
             [["200"], ["550"], DAYS_4], ids["mihoyo"]),
        Task("mihoyo-dup", "conflict", "小红书上米哈游补招实习的帖子有几条？它们说的是不是同一件事？请写出每条的编号。",
             [["3", "三"], ["同一", "相同", "一样", "重复", "搬运", "转发", "转载"]], ids["mihoyo"]),
        Task("abstain-meituan", "abstain", "美团有没有 Agent 方向的日常实习？", [NOT_FOUND]),
        Task("irrelevant-translate", "irrelevant", "把这句话翻译成英文：我在找一份 Agent 开发实习。", expect_tools=False),
        Task("recovery-ratelimit", "recovery", q_tx, [["Coding Agent Research Intern"]], ids["tx_coding"],
             faults={"jobs_search": ["rate_limit"]}),
        Task("recovery-timeout", "recovery", q_didi,
             [["上下文压缩", "workflow", "Workflow", "langgraph", "LangGraph", "benchmark", "Benchmark", "最大子数组"]],
             ids["didi"], faults={"posts_search": ["timeout"]}),
    ]
    missing = [t.id for t in tasks if t.expect_tools and t.category not in ("abstain",) and not t.cite]
    if missing:
        raise RuntimeError(f"库里缺这些题依据的条目，先采集并抽取：{', '.join(missing)}")
    return tasks
