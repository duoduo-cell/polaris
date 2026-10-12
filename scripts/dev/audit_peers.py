# 用法：python scripts/dev/audit_peers.py peer-v3   只读真实库、只在本机打印（原文是 B 档内容，不要贴进仓库）
"""验收：抽出来的"人和结果"对作者有没有用（标准事先定死，见 DEVLOG 第 1 轮）。只读真实库，只在本机打印。"""
import random
import re
import sys

import psycopg

AI = re.compile(r"ai|大模型|llm|agent|算法|后训练|rl|强化|rag|nlp|多模态|seed|豆包|通义|混元|文心|模型|智能体|推理|训练", re.I)
c = psycopg.connect("postgresql://polaris:polaris@127.0.0.1:5432/polaris")
version = sys.argv[1] if len(sys.argv) > 1 else "peer-v2"
rows = c.execute("""select p.id, p.raw_item_id, p.source, p.published_at, p.who, p.stage, p.school, p.papers, p.paper_summary, p.top_papers,
  p.internships, p.target_company, p.big_company, p.target_team, p.target_role, p.job_type, p.direction, p.outcome, p.reached,
  p.difficulty, p.dates, p.evidence, p.from_image, (r.raw_json ? 'deep') as deep
  from peer_profiles p join raw_items r on r.id = p.raw_item_id where p.prompt_version = %s""", (version,)).fetchall()
cols = ["id", "raw", "source", "published", "who", "stage", "school", "papers", "psum", "top", "ints", "company", "big", "team", "role",
        "jt", "dir", "outcome", "reached", "diff", "dates", "evidence", "img", "deep"]
P = [dict(zip(cols, r)) for r in rows]
posts = c.execute("select count(distinct raw_item_id) from peer_profiles where prompt_version=%s", (version,)).fetchone()[0]
read = c.execute("select count(*) from llm_calls where purpose='extract_peer' and prompt_version=%s", (version,)).fetchone()[0]
print(f"读了约 {read} 条帖子，{posts} 条里有具体的人，共 {len(P)} 个人")


def ai(p):
    return bool(AI.search(" ".join(str(p[k] or "") for k in ("dir", "role", "team", "company"))))


def has_bg(p):
    return bool((p["stage"] != "未说明" and p["school"]) or p["papers"] or p["psum"] or p["ints"])


def has_result(p):
    return p["outcome"] in ("拿到offer", "挂了", "进行中")


useful = [p for p in P if ai(p) and has_result(p) and has_bg(p)]
print(f"方向是 AI 类 {sum(map(ai, P))}；有结果 {sum(map(has_result, P))}；有背景 {sum(map(has_bg, P))}；三条都满足（对你有用）{len(useful)}")
from datetime import UTC, datetime
now = datetime.now(UTC)
for name, group in (("全部", P), ("有用", useful)):
    d30 = sum(1 for p in group if p["published"] and (now - p["published"]).days <= 30)
    d90 = sum(1 for p in group if p["published"] and (now - p["published"]).days <= 90)
    print(f"  {name}：最近 30 天 {d30}，最近 90 天 {d90}")
print("有用的里：", {"写了论文": sum(1 for p in useful if p["papers"] or p["psum"]), "有顶会/CCF-A": sum(1 for p in useful if p["top"]),
                  "写了实习": sum(1 for p in useful if p["ints"]), "写了学校": sum(1 for p in useful if p["school"]),
                  "拿到offer": sum(1 for p in useful if p["outcome"] == "拿到offer"), "挂了": sum(1 for p in useful if p["outcome"] == "挂了"),
                  "来自图片": sum(1 for p in useful if p["img"])})
print("阶段：", {s: sum(1 for p in useful if p["stage"] == s) for s in ("本科", "硕士", "博士", "已工作", "未说明")})
focus = {"字节 Seed/豆包": lambda p: re.search(r"seed|豆包", f"{p['team']} {p['role']} {p['dir']} {p['company']}", re.I),
         "后训练/RL": lambda p: re.search(r"后训练|rl|强化|post-?train|对齐", f"{p['role']} {p['dir']} {p['team']}", re.I),
         "Agent": lambda p: re.search(r"agent|智能体", f"{p['role']} {p['dir']} {p['team']}", re.I),
         "本科阶段": lambda p: p["stage"] == "本科"}
print("命中你的关注点（有用的里）：", {k: sum(1 for p in useful if f(p)) for k, f in focus.items()})
print("按公司（有用的里）：", sorted(((b, sum(1 for p in useful if p["big"] == b)) for b in {p["big"] for p in useful if p["big"]}), key=lambda x: -x[1])[:10])
random.seed(7)
sample = random.sample(useful, min(20, len(useful)))
print("\n=== 抽样 20 条（逐条对原文）===")
for p in sample:
    raw = c.execute("select raw_text from raw_items where id=%s", (p["raw"],)).fetchone()[0]
    print(f"#{p['id']} R-{p['raw']} {p['published'].date() if p['published'] else ''} {p['who']} | {p['stage']} 学校={p['school']} 论文={p['psum'] or len(p['papers'])} "
          f"实习={[i.get('company') for i in p['ints']]} | 目标={p['company']}/{p['team']}/{p['role']} {p['jt']} {p['dir']} | {p['outcome']} {p['reached'] or ''} | 图={p['img']}")
    print(f"   原句: {p['evidence'][:200]!r}")
    print(f"   原文: {raw[:600]!r}")
