"""在评测库 polaris_live 里用作者真实背景（data/profile.md，不进仓库）问"同阶段的人"问题，打印工具调用和回答。
先把 DATABASE_URL 指向 polaris_live，再运行：
uv run python -X utf8 scripts/dev/peer_chat_check.py --local-only --output data/probe/peer-check.json "问题1" "问题2"
回答出来后要把里面的事实（R- 编号、学历、实习、日期、原话）逐条回原文核对，不能只看格式。"""
import argparse
import json
import time

from polaris.agent import main
from polaris.config import ROOT, get_settings

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--local-only", action="store_true", help="只验证本地证据，不打开网页或占用采集浏览器")
parser.add_argument("--output", help="结果 JSON 保存在本机 data/ 下")
parser.add_argument("questions", nargs="*")
args = parser.parse_args()
assert get_settings().database_url.rsplit("/", 1)[-1] == "polaris_live", "只在评测库里跑"
INTRO = (ROOT / "data" / "profile.md").read_text(encoding="utf-8").strip()
destination = (ROOT / args.output).resolve() if args.output else None
if destination:
    assert destination.is_relative_to((ROOT / "data").resolve()), "真实问答只保存在本机 data/ 下"
if args.local_only:
    main.TOOLS = tuple(t for t in main.TOOLS if t not in ("source_open", "platform_search"))
QUESTIONS = args.questions or ["最近一个月拿到大厂 Agent 或大模型实习 offer 的人都是什么背景？跟我比我差在哪？",
                             "字节的算法或后训练实习，最近有人面过吗？面了几轮、难不难、他们什么背景？"]
thread = f"peertest-{int(time.time())}"
records = []


def save():
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({"prompt_version": main.PROMPT_VERSION, "local_only": args.local_only,
                                           "records": records}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


main.ready()
intro_turn = main.chat(INTRO, thread, on_event=lambda e: None)
if intro_turn.error:
    records.append({"stage": "profile", "error": intro_turn.error})
    save()
    print("背景初始化失败：", intro_turn.error)
    raise SystemExit(2)  # 服务不可用时不继续重复消耗重试
failed = False
for q in QUESTIONS:
    t0 = time.monotonic()
    turn = main.chat(q, thread, on_event=lambda e: None)
    print("=" * 80)
    print("问：", q, f"（{time.monotonic() - t0:.0f} 秒）")
    for c in turn.tool_calls:
        print("  工具：", c["tool"], json.dumps(c["args"], ensure_ascii=False)[:160], "ok" if c["ok"] else f"失败 {c.get('error_kind')}")
    print("答：", turn.answer)
    records.append({"question": q, "elapsed_seconds": round(time.monotonic() - t0, 2), "turn": turn.to_dict(),
                    "tool_evidence": turn.tool_calls})
    save()
    if turn.error or not turn.answer.strip():
        failed = True
        print("验收未完成：", turn.error or "没有正式回答")
        break
raise SystemExit(2 if failed else 0)
