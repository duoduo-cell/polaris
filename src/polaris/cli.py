"""命令行：uv run polaris --help"""
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
                 max_calls: int = typer.Option(40, help="本轮最多调用 boss-cli 几次（搜索和详情都算）"),
                 max_pages: int = typer.Option(1, help="每个 城市×规模 组合翻几页（每页 15 条）"),
                 cities: str = typer.Option("北京,上海,深圳,杭州", help="逗号分隔"),
                 job_type: str = typer.Option("实习", help="实习、全职；填 不限 表示不筛")) -> None:
    """Boss直聘：按 城市×公司规模 搜，只留大厂名单里的岗位并打开详情。需要本机 boss-cli 已扫码登录。"""
    from polaris.collectors.boss import BossCollector

    collector = BossCollector(cities=tuple(c.strip() for c in cities.split(",") if c.strip()), max_calls=max_calls,
                              job_type=None if job_type == "不限" else job_type)
    _collect("boss", collector, queries, {"max_calls": max_calls, "max_pages": max_pages, "cities": cities,
                                          "job_type": job_type})
    if collector.stats.stopped:
        typer.echo(f"本轮提前停下：{collector.stats.stopped}"
                   + ("（登录过期，请重新运行 boss login --qrcode）" if collector.stats.stopped == "not_authenticated" else ""))


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
