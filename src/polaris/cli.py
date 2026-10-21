"""命令行：uv run polaris --help"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

import typer

from polaris.config import ROOT, get_settings
from polaris.ingest import IngestResult, RawItemIn, ingest_raw_items
from polaris.runs import run_scope
from polaris.synth.jobs import FIXTURE, read_fixture

if (sys.stdout.encoding or "").lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(help="Polaris Core 命令行", no_args_is_help=True, add_completion=False)
metrics_app = typer.Typer(help="各模块的指标，结果写到 eval/reports/", no_args_is_help=True)
app.add_typer(metrics_app, name="metrics")
agent_app = typer.Typer(help="agent", no_args_is_help=True)
app.add_typer(agent_app, name="agent")
browser_app = typer.Typer(help="采集专用的浏览器（Edge，单独的资料夹）", no_args_is_help=True)
app.add_typer(browser_app, name="browser")
collect_app = typer.Typer(help="采集：每轮挑最久没搜过的查询词，采到的写进 raw_items", no_args_is_help=True)
app.add_typer(collect_app, name="collect")


def _collect(source: str, collector, n_queries: int, params: dict) -> dict:
    from polaris.collectors.rounds import collect_round

    return collect_round(source, collector, n_queries, params, echo=typer.echo)


@collect_app.command("all")
def collect_all(sources: str = typer.Option("tencent,sites,xhs", help="逗号分隔：tencent、sites（字节和小红书官网）、xhs、boss"),
                xhs_calls: int = typer.Option(20, help="小红书本轮最多调用几次"),
                boss_calls: int = typer.Option(30, help="Boss 本轮最多调用几次")) -> None:
    """每个来源一个侦察子 agent 并行采集，采完抽取，小红书侦察顺便提新查询词。"""
    from polaris.agent.scouts import run_scouts

    names = [x.strip() for x in sources.split(",") if x.strip()]
    reports = run_scouts(names, {"xhs": {"max_calls": xhs_calls}, "boss": {"max_calls": boss_calls}})
    for r in reports:
        if not r["ok"]:
            typer.echo(f"[{r['source']}] 失败：{r['error']}")
            continue
        line = (f"[{r['source']}] 新写入 {r['new']} 条，抽取 {r['extracted']} 条（失败 {r['extract_failed']}），"
                f"提到大厂 {r['big_company_items']} 条，采集失败 {r['failures']} 次，限流 {r['rate_limited']} 次")
        if r.get("stopped"):
            line += f"，提前停下：{r['stopped']}"
        if r.get("discovered_queries"):
            line += f"；新查询词：{'、'.join(r['discovered_queries'])}"
        typer.echo(line)


@collect_app.command("xhs")
def collect_xhs(queries: int = typer.Option(3, help="本轮搜几个查询词"),
                max_calls: int = typer.Option(20, help="本轮最多调用小红书工具几次（每次约 48 秒）"),
                publish_time: str = typer.Option("一周内", help="只看多久以内发布的：一天内、一周内、半年内、不限")) -> None:
    """小红书：横向搜笔记，纵向挖正文、评论和作者主页。需要本机 xhs-mcp 已登录。"""
    from polaris.collectors.xhs import XhsCollector

    _collect("xhs", XhsCollector(max_calls=max_calls, publish_time=publish_time), queries,
             {"max_calls": max_calls, "publish_time": publish_time})


@collect_app.command("xhs-day")
def collect_xhs_day(max_calls: int = typer.Option(800, help="今天最多调用小红书工具几次（每次约 40 秒，一次一个）"),
                    until: str = typer.Option("23:00", help="几点停，本机时间"),
                    round_queries: int = typer.Option(4, help="每轮搜几个查询词"),
                    publish_time: str = typer.Option("半年内", help="一天内、一周内、半年内、不限"),
                    sorts: str = typer.Option("最新,最多点赞,最多收藏", help="每个词按哪几种排序各搜一次")) -> None:
    """小红书连续采一天：一轮一轮地搜，每轮采完就抽取、提新查询词；到上限、到点或出现被拦的迹象就停。"""
    from datetime import datetime

    from polaris.agent.scouts import discover_queries
    from polaris.collectors.rounds import collect_round
    from polaris.collectors.xhs import XhsCollector, known_note_ids
    from polaris.extract import run_extract

    hh, mm = (int(x) for x in until.split(":"))
    deadline = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    known, used, rounds, total_new, stop = known_note_ids(), 0, 0, 0, None
    sort_list = tuple(x.strip() for x in sorts.split(",") if x.strip())
    per_query = 3 * len(sort_list) + 20  # 每个词：几次搜索 + 最多打开二十来条
    while used < max_calls and datetime.now() < deadline and stop is None:
        budget = min(round_queries * per_query, max_calls - used)
        c = XhsCollector(max_calls=budget, publish_time=publish_time, sorts=sort_list, pause=(2.0, 5.0), known=known)
        rounds += 1
        typer.echo(f"第 {rounds} 轮（已用 {used}/{max_calls} 次）{datetime.now():%H:%M}")
        r = collect_round("xhs", c, round_queries, {"max_calls": budget, "publish_time": publish_time,
                                                     "sorts": list(sort_list), "mode": "day"}, echo=typer.echo)
        used += c.stats.calls
        total_new += len(r["new_ids"])
        stop = c.stats.stopped
        if r["new_ids"]:
            try:  # 抽取或提词出错不影响继续采
                out = run_extract("post", len(r["new_ids"]) + 10, r["run_id"], get_settings().llm_concurrency, "xhs")
                added = discover_queries(r["new_ids"], r["run_id"])
                typer.echo(f"  抽取 {out['extracted']} 条" + (f"；新查询词：{'、'.join(added)}" if added else ""))
            except Exception as e:  # noqa: BLE001
                typer.echo(f"  抽取或提词出错（采集继续，之后用 polaris extract post 补）：{e}"[:200])
        if c.stats.calls == 0 and stop is None:
            stop = "这一轮一次都没调用（查询词用完或预算为 0）"
    typer.echo(f"今天共 {rounds} 轮，调用 {used} 次，新采到 {total_new} 条笔记。")
    if stop:
        typer.echo(f"提前停下：{stop}。请在手机上看一眼小红书账号是否正常，没问题再继续。")


@collect_app.command("boss")
def collect_boss(queries: int = typer.Option(2, help="本轮搜几个查询词"),
                 max_calls: int = typer.Option(40, help="本轮最多打开几个页面（搜索页和详情页都算）"),
                 cities: str = typer.Option("北京,上海,深圳,杭州", help="逗号分隔"),
                 via: str = typer.Option("browser", help="browser：在采集专用的 Edge 里浏览（推荐）；cli：用 boss-cli")) -> None:
    """Boss直聘：按 城市×公司规模 搜，只留大厂名单里的岗位并打开详情。"""
    city_list = tuple(c.strip() for c in cities.split(",") if c.strip())
    params = {"max_calls": max_calls, "cities": cities, "via": via}
    if via == "cli":
        from polaris.collectors.boss import BossCollector

        collector = BossCollector(cities=city_list, max_calls=max_calls)
        _collect("boss", collector, queries, params)
    else:
        from polaris.browser import attached
        from polaris.collectors.boss_web import BossWebCollector

        with attached() as context:
            collector = BossWebCollector(context, cities=city_list, max_calls=max_calls)
            _collect("boss", collector, queries, params)
    if collector.stats.stopped:
        typer.echo(f"本轮提前停下：{collector.stats.stopped}。请在采集浏览器里看一眼 Boss 页面是否正常，"
                   "有验证就你自己完成，然后再继续。")


@app.command()
def deepen(limit: int = typer.Option(40, help="这一批最多深挖几条")) -> None:
    """小红书深挖：重新打开已采到的笔记，读图片里的字、把评论翻完（和常驻采集共用锁和额度，会调读图模型）。"""
    from polaris.collectors.xhs_deep import deepen as run

    out = run(limit)
    items = out["items"]
    typer.echo(f"候选 {out['candidates']} 条，深挖 {out['deepened']} 条" + (f"，停在：{out['stopped']}" if out["stopped"] else ""))
    if items:
        live = [i for i in items if not i.get("gone")]
        typer.echo(f"  已下线 {len(items) - len(live)} 条；有图 {sum(1 for i in live if i['images'])} 条，"
                   f"图里有字 {sum(1 for i in live if i['images_with_text'])} 条，读出 {sum(i['ocr_chars'] for i in live)} 字；"
                   f"评论 {sum(i['comments_before'] for i in live)} → {sum(i['comments'] for i in live)} 条")


@browser_app.command("open")
def browser_open() -> None:
    """打开两个采集专用的 Edge：一个放小红书和脉脉，一个只放 Boss。第一次在里面各登录一次，之后保持开着即可。"""
    from polaris.browser import PROFILES, open_browser

    a = open_browser(["https://www.xiaohongshu.com/explore", "https://maimai.cn/"], profile="collect")
    b = open_browser(["https://www.zhipin.com/"], profile="boss")
    typer.echo(f"小红书和脉脉的窗口{'已打开' if a else '已经开着'}（资料夹 {PROFILES['collect'][0]}）；"
               f"Boss 的窗口{'已打开' if b else '已经开着'}（资料夹 {PROFILES['boss'][0]}）。"
               "第一次请在各自窗口里登录（扫码或验证码，你自己操作）；之后窗口开着即可，可以最小化。")


@browser_app.command("status")
def browser_status() -> None:
    """采集浏览器是否开着。"""
    from polaris.browser import is_running

    for profile, name in (("collect", "小红书和脉脉的窗口"), ("boss", "Boss 的窗口")):
        typer.echo(f"{name}：{'开着' if is_running(profile) else '没开，运行 polaris browser open'}")


@app.command()
def extract(kind: str = typer.Argument(..., help="job（岗位）或 post（小红书笔记）"),
            limit: int = typer.Option(50, help="本轮最多抽几条"),
            source: str = typer.Option(None, help="只抽某个来源，如 tencent:careers、xhs"),
            redo: bool = typer.Option(False, help="先删掉旧提示词版本抽的结果，再按当前版本重抽")) -> None:
    """把还没抽过的原始条目抽成结构化信息（会调模型）。"""
    from polaris.extract import drop_stale, run_extract

    if kind not in ("job", "post"):
        raise typer.BadParameter("只能是 job 或 post")
    with run_scope("extract", {"kind": kind, "limit": limit, "source": source, "redo": redo}) as (run_id, stats):
        if redo:
            stats["dropped_stale"] = drop_stale(kind, source)
            typer.echo(f"删掉旧版本抽取结果 {stats['dropped_stale']} 条")
        stats.update(run_extract(kind, limit, run_id, get_settings().llm_concurrency, source))
    typer.echo(f"待抽 {stats['pending']} 条，抽出 {stats['extracted']} 条，失败 {stats['failed']} 条，"
               f"属于大厂的 {stats['big_company']} 条。")


@collect_app.command("bytedance")
def collect_bytedance(queries: int = typer.Option(3, help="本轮搜几个方向词"),
                      max_pages: int = typer.Option(2, help="每个词翻几页（每页 50 条）")) -> None:
    """字节跳动校招官网（研发类日常实习）。用系统自带的 Edge 加载页面、读取接口返回。"""
    from polaris.collectors.sites import ByteDanceCollector

    _collect("bytedance", ByteDanceCollector(), queries, {"max_pages": max_pages})


@collect_app.command("xhs-careers")
def collect_xhs_careers(queries: int = typer.Option(2, help="本轮搜几个方向词"),
                        max_pages: int = typer.Option(3, help="列表翻几页（每页 10 条）")) -> None:
    """小红书招聘官网（研发方向）。逐个打开详情页读职责和要求。"""
    from polaris.collectors.sites import XhsCareersCollector

    _collect("xhs_careers", XhsCareersCollector(), queries, {"max_pages": max_pages})


@collect_app.command("tencent")
def collect_tencent(queries: int = typer.Option(4, help="本轮搜几个查询词"),
                    max_pages: int = typer.Option(2, help="每个查询词翻几页（每页 20 条）")) -> None:
    """腾讯招聘公开接口。"""
    from polaris.collectors.tencent import TencentCollector

    _collect("tencent", TencentCollector(), queries, {"max_pages": max_pages})


@app.command("db-upgrade")
def db_upgrade() -> None:
    """把数据库表结构迁移到最新版本。"""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    typer.echo("表结构已是最新。")


@app.command()
def synth(out: Path = typer.Option(FIXTURE, help="输出文件"),
          concurrency: int = typer.Option(0, help="并发数，0 表示用配置里的 LLM_CONCURRENCY"),
          limit: int = typer.Option(0, help="只生成前几条，调试用")) -> None:
    """用模型生成合成岗位数据（会调模型，花钱）。"""
    from polaris.llm import LLM
    from polaris.synth.jobs import PROMPT_VERSION, SEED, build_specs, generate, write_fixture

    specs = build_specs()
    if limit:
        specs = specs[:limit]
    workers = concurrency or get_settings().llm_concurrency
    with run_scope("synth", {"seed": SEED, "n": len(specs), "prompt_version": PROMPT_VERSION}) as (run_id, stats):
        records = generate(LLM(), specs, run_id, workers)
        write_fixture(records, out)
        stats.update({"records": len(records), "checked": sum(r["meta"]["checked"] for r in records),
                      "tries": dict(Counter(r["meta"]["tries"] for r in records))})
    typer.echo(f"生成 {len(records)} 条，核对通过 {stats['checked']} 条，写到 {out}")
    bad = [r["id"] for r in records if not r["meta"]["checked"]]
    if bad:
        typer.echo(f"没通过核对（评测时排除）：{', '.join(bad)}")


def seed_records(path: Path, run_id: int | None) -> IngestResult:
    items = [RawItemIn(source=r["source"], tier="A", kind="job", url=r["url"], external_id=r["id"],
                       raw_text=r["raw_text"], fetched_at=r["fetched_at"], is_synthetic=True,
                       raw_json={"synthetic_id": r["id"], "lang": r["lang"], "style": r["style"]})
             for r in read_fixture(path) if r["raw_text"]]
    return ingest_raw_items(items, run_id)


@app.command()
def seed(file: Path = typer.Option(FIXTURE, help="合成数据文件")) -> None:
    """把合成数据写进数据库。重复运行不会重复写入。"""
    with run_scope("seed", {"file": str(file.relative_to(ROOT) if file.is_relative_to(ROOT) else file)}) as (
            run_id, stats):
        result = seed_records(file, run_id)
        stats.update(result.model_dump(exclude={"ids"}))
    typer.echo(f"读到 {result.received} 条，新写入 {result.inserted} 条，已存在 {result.duplicates} 条。")


@app.command()
def status(items: int = typer.Option(5, help="每个来源显示最近几条")) -> None:
    """一眼看全：常驻采集在不在跑、两个采集窗口、各平台登录、今天的量、最近采到的内容。"""
    from polaris.browser import is_running
    from polaris.collectors import budget as b
    from polaris.collectors.boss import logged_in as boss_logged_in
    from polaris.daemon import single_instance
    from polaris.report import daily, latest

    lock = single_instance()
    running = lock is None
    if lock is not None:
        lock.close()
    typer.echo(f"常驻采集：{'在跑' if running else '没在跑（运行 polaris daemon，或等开机自启）'}")
    for profile, name in (("collect", "小红书和脉脉的窗口"), ("boss", "Boss 的窗口")):
        typer.echo(f"{name}：{'开着' if is_running(profile) else '没开（运行 polaris browser open）'}")
    logins = {"boss": boss_logged_in()}
    if is_running("collect"):
        from polaris.browser import attached
        from polaris.logins import logged_in

        with attached() as ctx:
            for src in ("xhs", "maimai"):
                logins[src] = logged_in(ctx, src)
    typer.echo("登录：" + "  ".join(f"{src} {'已登录' if ok else '没登录'}" for src, ok in logins.items()))
    d = daily()
    typer.echo("")
    typer.echo("今天：")
    for src in b.policy():
        cap, why = b.today_cap(src)
        raw = {"xhs": "xhs", "boss": "boss", "maimai": "maimai"}[src]
        typer.echo(f"  {src}：上限 {cap}，已用 {b.used_today(src)} 次，新采到 {d['new'].get(raw, 0)} 条，"
                   f"库里共 {d['total'].get(raw, 0)} 条（{why}）")
    typer.echo("  被拦或提前停下：" + ("；".join(d["stops"]) if d["stops"] else "没有"))
    for src in ("xhs", "maimai", "boss"):
        rows = latest(src, items)
        if rows:
            typer.echo("")
            typer.echo(f"最近采到的 {src}：")
            for when, title in rows:
                typer.echo(f"  {when}  {title}")


@app.command()
def daemon(log: Path = typer.Option(None, help="同时写进这个日志文件（UTF-8，每行带时间）")) -> None:
    """常驻采集：开着就行。按 config/collect.toml 的时间段和每天的量，轮流采脉脉、Boss、小红书；被拦或掉登录会弹通知。"""
    from datetime import datetime

    from polaris.daemon import loop

    def echo(line: str) -> None:
        typer.echo(line)
        if log:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                print(f"{datetime.now():%m-%d %H:%M:%S} {line}", file=f)

    loop(echo=echo)


@app.command()
def budget() -> None:
    """看今天每个来源的采集上限、为什么是这个数、已经用了多少。"""
    from polaris.collectors import budget as b

    for source in b.policy():
        cap, why = b.today_cap(source)
        typer.echo(f"{source}：今天上限 {cap}，已用 {b.used_today(source)}（{why}）")


@app.command()
def backfill() -> None:
    """给原始条目补上缺的字段（近似重复指纹、发布时间、采集方式），旧版本采集器写进来的也能补齐。"""
    from datetime import UTC, datetime

    from sqlalchemy import or_, select, update

    from polaris.db.models import RawItem
    from polaris.db.session import session_scope
    from polaris.dedup import simhash64

    methods = {"xhs": "mcp", "tencent:careers": "api", "bytedance:campus": "browser", "xhs:careers": "browser", "boss": "cli"}
    n = 0
    with session_scope() as s:
        rows = s.execute(select(RawItem).where(or_(RawItem.simhash.is_(None), RawItem.fetch_method.is_(None),
                                                   RawItem.published_at.is_(None) & (RawItem.source == "xhs")))).scalars()
        for r in rows:
            values = {}
            if r.simhash is None:
                values["simhash"] = simhash64(r.raw_text)
            if r.fetch_method is None:
                values["fetch_method"] = methods.get(r.source, "synthetic" if r.is_synthetic else "manual")
            note_time = ((r.raw_json or {}).get("note") or {}).get("time")
            if r.published_at is None and r.source == "xhs" and note_time:
                values["published_at"] = datetime.fromtimestamp(note_time / 1000, UTC)
            if values:
                s.execute(update(RawItem).where(RawItem.id == r.id).values(**values))
                n += 1
    typer.echo(f"补齐 {n} 条。")


@app.command()
def report() -> None:
    """今天的采集日报：各来源新增多少、涉及哪些大厂、有没有被拦。"""
    from polaris.report import daily, render

    typer.echo(render(daily()))


@app.command()
def align() -> None:
    """多来源对齐：把所有 posting 归成岗位、裁决字段冲突，然后在三份标准答案上算同岗识别的成绩（不调模型）。"""
    from polaris.align.build import run_align
    from polaris.eval import alignment

    typer.echo(json.dumps(run_align(), ensure_ascii=False))
    for name, path in (("开发集", alignment.GOLD), ("留出集", alignment.HOLDOUT), ("测试集", alignment.TEST)):
        r = alignment.pairwise(path)
        typer.echo(f"{name} {r['pairs']} 对（同岗 {r['positives']}，拿不准 {r['unsure']}）："
                   f"精确率 {r['precision']:.3f} 召回率 {r['recall']:.3f} F1 {r['f1']:.3f}")
    c, p = alignment.conflict_detection(), alignment.provenance()
    typer.echo(f"合成冲突发现 {c['found']}/{c['total']}；出处可追溯 {p['traceable']}/{p['fields']}")


def _show_turn(turn) -> None:
    for e in turn.memory_events:
        if e["op"] not in ("noop", "skip"):
            typer.secho(f"  [记忆] {e['op']} {e['attribute']}={e.get('value')}（{e['reason']}）", fg="cyan")
    for c in turn.tool_calls:
        typer.secho(f"  [工具] {c['tool']} {json.dumps(c['args'], ensure_ascii=False)[:120]}", fg="bright_black")
    if turn.error:
        typer.secho(f"  [出错] {turn.error[:300]}", fg="red")
    if turn.answer:
        typer.echo(turn.answer)


def _approve_loop(turn, thread: str):
    from polaris.agent.main import resume

    while turn.approvals:
        for a in turn.approvals:
            typer.secho(f"北极星想执行：{a['name']} {json.dumps(a['args'], ensure_ascii=False)}", fg="yellow")
        ok = typer.confirm("批准吗？", default=False)
        reason = "" if ok else typer.prompt("不批准的理由（可以留空）", default="", show_default=False)
        turn = resume(thread, ok, reason)
        _show_turn(turn)
    return turn


memory_app = typer.Typer(help="记忆：看、清空")
app.add_typer(memory_app, name="memory")


@memory_app.command("show")
def memory_show() -> None:
    """北极星现在记着的关于你的事，和等你确认的推断。"""
    from polaris.db.session import session_scope
    from polaris.memory.update import card, pending, render_card

    with session_scope() as s:
        typer.echo(render_card(card(s)))
        waiting = pending(s)
        if waiting:
            typer.echo("\n等你确认的：")
            for f in waiting:
                typer.echo(f"- {f.statement}  [M-{f.id}]")


@memory_app.command("reset")
def memory_reset(yes: bool = typer.Option(False, "--yes", help="不再确认")) -> None:
    """清空当前库里的全部记忆（事实、事件、抑制记录）和推送记录。测试时用；会先问你。"""
    from sqlalchemy import text

    from polaris.config import get_settings
    from polaris.db.session import session_scope

    db = get_settings().database_url.rsplit("/", 1)[-1]
    if not yes and not typer.confirm(f"清空库 {db} 里的全部记忆和推送记录？", default=False):
        raise typer.Exit()
    with session_scope() as s:
        s.execute(text("truncate notifications, memory_events, memory_facts, memory_suppressions restart identity cascade"))
    typer.echo(f"已清空 {db} 的记忆和推送记录。")


SANDBOX = "polaris_sandbox"


@app.command()
def sandbox(keep_memory: bool = typer.Option(False, help="连记忆一起复制（默认复制后清空记忆，从零开始）")) -> None:
    """把真实的库复制一份到 polaris_sandbox，随便测，不碰你的真实记忆。复制前先关掉连着沙盒库的 API。
    用沙盒：scripts/start.ps1 -Sandbox，或者先设 $env:DATABASE_URL 再运行命令。"""
    import subprocess

    from polaris.config import get_settings

    if SANDBOX in get_settings().database_url:
        typer.echo("现在连的就是沙盒库，换回真实的库再复制。")
        raise typer.Exit(1)

    def run(*args: str) -> None:
        r = subprocess.run(["docker", "exec", "polaris-db-1", *args], capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            typer.echo(f"出错了：{' '.join(args[:3])}：{r.stderr.strip()[:300]}")
            raise typer.Exit(1)

    typer.echo("导出真实的库……")
    run("pg_dump", "-U", "polaris", "-Fc", "-f", "/tmp/sandbox.dump", "polaris")
    run("psql", "-U", "polaris", "-d", "postgres", "-qc", f"drop database if exists {SANDBOX} with (force)")
    run("psql", "-U", "polaris", "-d", "postgres", "-qc", f"create database {SANDBOX}")
    typer.echo("恢复到沙盒库……")
    run("pg_restore", "-U", "polaris", "-d", SANDBOX, "--no-owner", "/tmp/sandbox.dump")
    # 克隆业务数据不应复制可执行的聊天队列，否则打开沙盒会自动重跑真实库里的待办。
    run("psql", "-U", "polaris", "-d", SANDBOX, "-qc",
        "DO $$ DECLARE t text; BEGIN FOREACH t IN ARRAY ARRAY['chat_tasks','chat_messages','chat_threads',"
        "'checkpoint_writes','checkpoint_blobs','checkpoints'] LOOP IF to_regclass(t) IS NOT NULL THEN "
        "EXECUTE format('TRUNCATE %I RESTART IDENTITY CASCADE', t); END IF; END LOOP; END $$")
    if not keep_memory:
        run("psql", "-U", "polaris", "-d", SANDBOX, "-qc",
            "truncate notifications, memory_events, memory_facts, memory_suppressions restart identity cascade")
    typer.echo(f"好了：{SANDBOX}（岗位、帖子、向量都有；聊天和任务已清空；记忆{'保留' if keep_memory else '是空的'}）。"
               "用它：.\\scripts\\start.ps1 -Sandbox")


@app.command()
def feedback(last: int = typer.Option(20, help="看最近几条")) -> None:
    """你在网页对话里标的"对 / 有问题"（存在 data/feedback/answers.jsonl，只在本机）。"""
    from polaris.config import ROOT

    path = ROOT / "data" / "feedback" / "answers.jsonl"
    if not path.exists():
        typer.echo("还没有。")
        return
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()][-last:]
    for r in rows:
        mark = "对" if r["verdict"] == "good" else "有问题"
        typer.echo(f"[{r['at'][:16]}] {mark}（{r['db']}，会话 {r['thread_id']}）问：{r['question'][:60]}")
        if r.get("note"):
            typer.echo(f"    你的说明：{r['note']}")


@app.command()
def serve(port: int = typer.Option(8000), reload: bool = typer.Option(False, help="改代码后自动重启（开发用）")) -> None:
    """在本机起 API（只绑 127.0.0.1）。和常驻采集同一个环境：能读 data/ 里的简报，能连本机的 Neo4j。"""
    import uvicorn

    uvicorn.run("polaris.api.app:app", host="127.0.0.1", port=port, reload=reload)


@app.command()
def phone(port: int = typer.Option(3000, help="网页的端口"),
          new: bool = typer.Option(False, help="换一个新的配对码（之前配对过的设备要重新扫）"),
          as_json: bool = typer.Option(False, "--json", help="只输出 JSON（给 start.ps1 用）"),
          qr: bool = typer.Option(False, help="生成二维码图片并用看图软件打开")) -> None:
    """手机模式：配对码、这台电脑在局域网和 Tailscale 里的地址、给手机扫的二维码。由 scripts/start.ps1 -Phone 调用。"""
    from polaris import phone as ph

    p = ph.info(port=port, new=new)
    if as_json:
        typer.echo(json.dumps({"code": p.code, "origins": p.origins, "via": p.via, "urls": p.urls}))
        return
    if not p.urls:
        typer.echo("没找到这台电脑在局域网里的地址：先连上 Wi-Fi（手机和电脑连同一个）。")
        raise typer.Exit(1)
    typer.echo(f"配对码：{p.code}（手机扫码就不用输）")
    for u in p.urls:
        typer.echo(f"  {u['label']}：{u['url']}")
    if qr:
        for path in ph.qr_images(p):
            typer.echo(f"  二维码：{path}")
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606  用系统的看图软件打开，给手机扫


@app.command()
def say(message: str, thread: str = typer.Option("main", help="会话编号，同一个编号接着聊")) -> None:
    """和北极星说一句话（先过记忆，再回答）。要批准的动作会在这里问你。"""
    from polaris.agent.main import chat

    turn = chat(message, thread)
    _show_turn(turn)
    _approve_loop(turn, thread)


@app.command()
def talk(thread: str = typer.Option("main", help="会话编号，同一个编号接着聊")) -> None:
    """和北极星连续对话。空行或 Ctrl+C 结束。"""
    from polaris.agent.main import chat

    typer.echo(f"会话 {thread}（空行结束）")
    while True:
        try:
            message = typer.prompt("你", default="", show_default=False)
        except (KeyboardInterrupt, EOFError):
            break
        if not message.strip():
            break
        turn = chat(message, thread)
        _show_turn(turn)
        _approve_loop(turn, thread)


@app.command()
def daily(dry_run: bool = typer.Option(False, help="只看决定，不记、不推、不写简报")) -> None:
    """主动推送：按你的卡片给最近的岗位和帖子打分，推 / 简报 / 不提，写今天的简报（data/briefs/）。"""
    from polaris.proactive.daily import run_daily

    r = run_daily(dry_run=dry_run)
    if r.get("skipped"):
        typer.echo(r["skipped"])
        return
    typer.echo(f"候选 {r['candidates']}：推 {r['push']}、简报 {r['brief']}、不提 {r['drop']}、放不下 {r['not_listed']}"
               + ("（第一次跑，只出简报不推送）" if r["first_run"] else ""))
    for d in r["decisions"]:
        if d["channel"] != "drop":
            typer.echo(f"  [{d['channel']}] {d['cite']} {d['title'][:40]}：{'；'.join(d['reasons'][:2])}")


@metrics_app.command("m1")
def metrics_m1() -> None:
    """M1：追踪完整率、结构化输出、延迟、合成数据核对、入库幂等、缓存。"""
    from polaris.eval import m1

    stats = m1.run()
    typer.echo((m1.REPORT_DIR / "M1.md").read_text(encoding="utf-8"))
    typer.echo(f"已写到 {m1.REPORT_DIR / 'M1.md'}（run {stats.get('synth', {}).get('run_id', '—')}）")


@metrics_app.command("m2")
def metrics_m2(k: int = typer.Option(3, help="每题跑几次，用来算 pass^k")) -> None:
    """M2：工具调用套件（会调模型）、出错恢复、追踪完整率、回放一致性。"""
    from polaris.eval import m2

    m2.run(k=k)
    typer.echo((m2.REPORT_DIR / "M2.md").read_text(encoding="utf-8"))


@metrics_app.command("m3")
def metrics_m3() -> None:
    """M3：采集覆盖、重复、大厂占比、限流；抽取逐字段对标准答案（不调模型）。"""
    from polaris.eval import m3

    m3.run()
    typer.echo((m3.REPORT_DIR / "M3.md").read_text(encoding="utf-8"))


@metrics_app.command("m3-tools")
def metrics_m3_tools(k: int = typer.Option(3, help="每题跑几次，用来算 pass^k")) -> None:
    """M3：真实数据上的工具调用套件 real-v1（会调模型），然后重出 M3 报告。"""
    from polaris.eval import m3

    m3.run_tools(k=k)
    m3.run()
    typer.echo((m3.REPORT_DIR / "M3.md").read_text(encoding="utf-8"))


@metrics_app.command("m4")
def metrics_m4() -> None:
    """M4：对齐（三份标准答案）、冲突发现、出处；检索各配置的消融对比（会调向量和重排，评审结果读缓存）。"""
    from polaris.eval import m4

    m4.run()
    typer.echo(m4.REPORT.read_text(encoding="utf-8"))


@app.command("knowledge-status")
def knowledge_status() -> None:
    """只读查看知识待办、步骤结果、错误、到期时间和次数；不触发模型或采集。"""
    from polaris.search.maintenance import status

    typer.echo(json.dumps(status(), ensure_ascii=False, indent=2))


@app.command("knowledge-retry")
def knowledge_retry() -> None:
    """只将耗尽的知识待办重新排队；修好原因后人工使用，不启动采集。"""
    from polaris.search.maintenance import retry

    typer.echo(json.dumps(retry(), ensure_ascii=False, indent=2))


@app.command("knowledge-diagnose")
def knowledge_diagnose() -> None:
    """全量只读检查历史未抽取、对齐、切块／题目及向量积压；不调用模型或采集。"""
    from polaris.search.audit import diagnose

    typer.echo(json.dumps(diagnose(), ensure_ascii=False, indent=2))


@app.command("knowledge-enqueue")
def knowledge_enqueue(allow_prune: bool = typer.Option(False, help="许可本次诊断的旧岗位清理；执行时复核，人工跟进／事件不可清理")) -> None:
    """诊断后显式排队历史知识积压；不执行。已有待办不重置，耗尽需独立 retry。"""
    from polaris.search.audit import enqueue_refresh

    typer.echo(json.dumps(enqueue_refresh(allow_prune=allow_prune), ensure_ascii=False, indent=2))


@app.command("knowledge-work")
def knowledge_work() -> None:
    """执行一遍已到期知识待办（可能调用向量服务），不启动采集／浏览器。"""
    from polaris.search.maintenance import run_pending

    typer.echo(json.dumps(run_pending(), ensure_ascii=False, indent=2))


@app.command()
def index(embed: bool = typer.Option(True, help="同时给新块算向量"), graph: bool = typer.Option(True, help="同时重建知识图谱")) -> None:
    """检索的底子：jobs 和 posts 切块写进 chunks、算向量，重建 Neo4j 知识图谱。都可以重复跑。"""
    from polaris.search.index import embed_pending, sync_chunks

    typer.echo(json.dumps({"chunks": sync_chunks()}, ensure_ascii=False))
    if embed:
        typer.echo(json.dumps({"embed": embed_pending()}, ensure_ascii=False))
    if graph:
        from polaris.graph.client import enabled

        if enabled():
            from polaris.graph.build import rebuild

            typer.echo(json.dumps({"graph": rebuild()}, ensure_ascii=False, default=str))
        else:
            typer.echo("没有配置 NEO4J_PASSWORD，跳过知识图谱")


@app.command()
def search(query: str, scope: str = typer.Option("all", help="all / jobs / posts / questions"),
           company: str = typer.Option(None, help="只看这家公司"), top_k: int = typer.Option(10)) -> None:
    """混合检索（关键词 + 向量 + 图谱，重排，去冗余），打印每条的各路名次和分数。"""
    from polaris.search.retrieve import SearchQuery
    from polaris.search.retrieve import search as run_search

    hits, trace = run_search(SearchQuery(query, scope=scope, company=company, top_k=top_k))  # type: ignore[arg-type]
    typer.echo(json.dumps(trace, ensure_ascii=False))
    for h in hits:
        rr = "—" if h.rerank is None else f"{h.rerank:.2f}"
        typer.echo(f"{h.final:.3f}  重排 {rr}  融合 {h.fused:.2f}  {h.ranks}  {h.header}")


@metrics_app.command("m4-bench")
def metrics_m4_bench(k: int = typer.Option(3, help="每题跑几次，用来算 pass^k")) -> None:
    """M4b：Polaris-Bench v2 难题（会调模型），knowledge 和 search 两种配置对比，然后重出 M4 报告。"""
    from polaris.eval import m4

    m4.run_bench(k=k)
    m4.REPORT.write_text(m4.render(json.loads(m4.REPORT_JSON.read_text(encoding="utf-8"))), encoding="utf-8")
    typer.echo(m4.REPORT.read_text(encoding="utf-8"))


@metrics_app.command("m5")
def metrics_m5(k: int = typer.Option(1, help="每个问题问几次")) -> None:
    """M5：记忆评测（会调模型）。在单独的 polaris_memeval 库里跑，不碰你自己的记忆。"""
    from polaris.eval import memory_eval

    results = {name: memory_eval.run(k=k, gold_path=path) for name, path in memory_eval.SETS}
    memory_eval.write_report(results)
    typer.echo((memory_eval.REPORT_DIR / "M5.md").read_text(encoding="utf-8"))


@metrics_app.command("answers")
def metrics_answers(fresh: bool = typer.Option(False, help="先清空这个评测库的聊天和记忆（只在单独的库上）"),
                    workers: int = typer.Option(4, help="几题并行"),
                    only: str = typer.Option("", help="只跑这些题，逗号分隔，如 N1,E3"),
                    spec: str = typer.Option("answers_v1", help="题目（eval/gold/ 下）：answers_v1 开发集，answers_v2 留出集"),
                    rejudge: str = typer.Option("", help="不重跑回答，只重新打分：data/probe/answers-… 目录"),
                    out: str = typer.Option("", help="报告文件名（eval/reports/ 下，不带扩展名），默认按题目定")) -> None:
    """回答质量：正常、边界、对抗三类题，规则检查 + 另一家模型打分（会调模型；只在单独的库上跑）。"""
    from pathlib import Path

    from polaris.config import ROOT
    from polaris.eval import answer_eval

    spec_path = ROOT / "eval" / "gold" / f"{spec}.json"
    if rejudge:
        result = answer_eval.rejudge(Path(rejudge), spec_path=spec_path, workers=workers)
    else:
        result = answer_eval.run(spec_path=spec_path, workers=workers, fresh=fresh,
                                 only=[x.strip() for x in only.split(",") if x.strip()] or None)
    if out:
        result["out"] = out
    typer.echo(answer_eval.write_report(result))


@metrics_app.command("proactive")
def metrics_proactive() -> None:
    """主动决策：开发集和留出集场景上，推 / 简报 / 不提的准确率、打扰率、漏报率（不调模型）。"""
    from polaris.eval import proactive_eval

    typer.echo(proactive_eval.write_report())


@metrics_app.command("injection")
def metrics_injection(k: int = typer.Option(3, help="每种攻击跑几次")) -> None:
    """提示注入：帖子和职位描述里藏着删记忆、外传数据的指令，主 agent 会不会照做（会调模型，在单独的 polaris_inject 库里跑）。"""
    from polaris.eval import injection

    typer.echo(injection.write_report(injection.run(k=k)))


@metrics_app.command("market")
def metrics_market() -> None:
    """大厂 Agent 岗位要什么技术、面经里问什么（按采到的数据统计，不调模型）。"""
    from polaris.eval import market

    typer.echo(market.run())


@agent_app.command("ask")
def agent_ask(question: str,
              record: Path = typer.Option(None, help="把这次运行录下来，存到这个文件"),
              replay: Path = typer.Option(None, help="从这个录像回放工具调用"),
              full: bool = typer.Option(False, help="和 --replay 一起用：模型回答也从录像取，不调任何服务")) -> None:
    """问最小 agent 一个问题（它只会查原始条目）。"""
    from polaris.agent.minimal import ask
    from polaris.replay import Cassette

    cassette = (Cassette(record, "record") if record else
                Cassette(replay, "replay", llm_replay=full) if replay else None)
    with run_scope("agent", {"question": question, "replay": str(replay) if replay else None}) as (run_id, stats):
        r = ask(question, run_id=run_id, cassette=cassette)
        if record and cassette is not None:
            cassette.save()
        stats.update({"tool_calls": len(r.tool_calls), "latency_ms": r.latency_ms, "error": r.error})
    for c in r.tool_calls:
        mark = "回放" if c["replayed"] else ("成功" if c["ok"] else f"失败:{c['error_kind']}")
        typer.echo(f"  [{mark}] {c['tool']} {c['args']}")
    typer.echo(r.error or r.answer)


if __name__ == "__main__":
    app()
