"""常驻采集：开着就行，不用每次手动开浏览器、登录。

每隔一会儿看一遍各来源：在采集时间段内、今天的量没用完、还登录着，就跑一轮（几个查询词）；
采完马上抽取，小红书再提新查询词。被拦就停这个来源到明天并弹通知；掉登录就弹通知等你扫码，别的来源照常。
每天的量按 config/collect.toml 慢慢加（budget.py）。
"""
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from polaris.browser import attached, is_running, open_browser
from polaris.collectors import budget
from polaris.collectors.rounds import collect_round
from polaris.config import ROOT, get_settings
from polaris.logins import logged_in, notify
from polaris.report import BEIJING

LOCK_FILE = ROOT / "data" / "daemon.lock"

# Boss 走 boss-cli（调搜索和详情接口），登录从 Boss 专用的 Edge 窗口拿；我们的程序不接管那个窗口（browser.py）。
IMPLEMENTED = ("maimai", "boss", "xhs")  # 一轮快的先跑：脉脉几分钟、Boss 看令牌、小红书最慢
POST_SOURCES = {"xhs": ("post", "xhs"), "boss": ("job", "boss"), "maimai": ("post", "maimai")}


def _xhs_collector(context: Any, cap_left: int, cfg: dict[str, Any]):
    from polaris.collectors.xhs import XhsCollector, call_json
    from polaris.collectors.xhs_browser import BrowserCaller

    caller = BrowserCaller(context) if cfg.get("via", "browser") == "browser" else call_json
    return XhsCollector(max_calls=cap_left, publish_time="半年内", sorts=("最新", "最多点赞", "最多收藏"),
                        pause=tuple(cfg["pause"]), caller=caller)


def _boss_collector(context: Any, cap_left: int, cfg: dict[str, Any]):
    from polaris.collectors.boss import BossCollector

    return BossCollector(max_calls=cap_left, pause=tuple(cfg["pause"]))


def _maimai_collector(context: Any, cap_left: int, cfg: dict[str, Any]):
    from polaris.collectors.maimai import MaimaiCollector

    return MaimaiCollector(context, max_calls=cap_left, pause=tuple(cfg["pause"]))


FACTORIES: dict[str, Callable[[Any, int, dict[str, Any]], Any]] = {
    "xhs": _xhs_collector, "boss": _boss_collector, "maimai": _maimai_collector}


def run_round(source: str, context: Any, cap: int, used: int, echo: Callable[[str], None] = print) -> dict[str, Any]:
    from polaris.agent.scouts import discover_queries
    from polaris.extract import run_extract

    cfg = budget.policy()[source]
    per_query = {"xhs": 3 * 3 + 20, "boss": 30, "maimai": 10}[source]
    left = min(cap - used, cfg["round_queries"] * per_query)
    left = min(left, budget.hour_left(source))
    # 小红书每轮留三分之一补老笔记（读图、翻评论，见 collectors/xhs_deep.py），新采的笔记打开时已经顺手补全
    reserve = left // 3 if source == "xhs" else 0
    collector = FACTORIES[source](context, left - reserve, cfg)
    from polaris.collectors.locks import platform_lock

    try:
        with platform_lock(source, timeout=600):  # 和对话里的现查共用一把锁，同一个平台不同时操作
            r = collect_round(source, collector, cfg["round_queries"],
                              {"max_calls": left - reserve, "cap": cap, "via": cfg.get("via", "browser"), "mode": "daemon"},
                              echo=echo)
            if reserve and not r.get("stopped") and hasattr(getattr(collector, "caller", None), "page"):
                try:
                    from polaris.collectors.xhs_deep import backfill

                    b = backfill(collector.caller, reserve)
                    if b["deepened"] or b["stopped"]:
                        echo(f"  [{source}] 补老笔记 {b['deepened']} 条" + (f"，停在：{b['stopped']}" if b["stopped"] else ""))
                    if b["stopped"]:
                        r["stopped"] = b["stopped"]
                except Exception as e:  # noqa: BLE001  补老笔记出错不影响采集
                    echo(f"  [{source}] 补老笔记出错：{e}"[:200])
    finally:  # 每轮结束关掉自己开的标签页
        for obj in (collector, getattr(collector, "caller", None)):
            if hasattr(obj, "close"):
                obj.close()
    kind, raw_source = POST_SOURCES[source]
    data_changed = bool(r["new_ids"])
    try:  # 当前批次优先，留名额限量补处理历史积压。
        out = run_extract(kind, len(r["new_ids"]) + 10, r["run_id"], get_settings().llm_concurrency, raw_source)
        data_changed = data_changed or out["extracted"] > 0
        echo(f"  [{source}] 抽取 {out['extracted']} 条，失败 {out.get('failed', 0)} 条")
        added = discover_queries(r["new_ids"], r["run_id"]) if source == "xhs" and r["new_ids"] else []
        if added:
            echo(f"  [{source}] 新查询词：{'、'.join(added)}")
        from polaris.peers import run as extract_peers

        peers = extract_peers(len(r["new_ids"]) + 20, source=raw_source)  # 帖子里"人和结果"（背景、offer、几面）
        data_changed = data_changed or peers["people"] > 0
        if peers["people"]:
            echo(f"  [{source}] 人和结果：{peers['with_people']} 条帖子里有 {peers['people']} 个人")
    except Exception as e:  # noqa: BLE001
        echo(f"  [{source}] 抽取或提词出错（采集继续）：{e}"[:200])
    if data_changed:
        from polaris.search.maintenance import request_refresh

        request_refresh(f"daemon:{source}")  # 先持久化，失败／退出后无新增也能继续
        maintain_knowledge(echo)
        proactive(echo, f"  [{source}] ")
    return r


def maintain_knowledge(echo: Callable[[str], None] = print) -> None:
    """只做已排队的本地知识维护，不依赖采集窗口、浏览器或平台认证。"""
    from polaris.search.maintenance import run_pending

    try:
        out = run_pending()
        if out["ran"]:
            stats = out.get("stats", {})
            error = stats.get("error") or stats.get("graph_error")
            echo(f"[知识层] 待办 {out['run_id']}：{out['status']}"
                 + (f"；{error}"[:220] if error else ""))
    except Exception as e:  # noqa: BLE001  数据库等维护故障不能阻止采集
        echo(f"[知识层] 维护出错：{e}"[:220])


def proactive(echo: Callable[[str], None], prefix: str = "") -> None:
    """主动推送（M6）：新数据进来后判断值不值得打扰你；卡片里还没有方向和城市时什么也不做。出错不影响采集。"""
    from polaris.proactive.daily import run_daily

    try:
        r = run_daily()
    except Exception as e:  # noqa: BLE001
        echo(f"{prefix}主动推送出错（采集继续）：{e}"[:200])
        return
    if not r.get("skipped"):
        echo(f"{prefix}主动推送：推 {r['push']}、进简报 {r['brief']}、不提 {r['drop']}")


OFFICIAL_RAW = {"tencent": "tencent:careers", "bytedance": "bytedance:campus", "xhs_careers": "xhs:careers"}
OFFICIAL_RETRY_COOLDOWN = timedelta(hours=1)


def _official_runs_today(now: datetime) -> list[tuple[str, str, datetime]]:
    from sqlalchemy import select

    from polaris.db.models import Run
    from polaris.db.session import session_scope

    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with session_scope() as s:
        return list(s.execute(select(Run.params["source"].astext, Run.status, Run.started_at).where(
            Run.kind == "collect", Run.params["mode"].astext == "official-daily", Run.started_at >= start)).all())


def official_pending_sources(now: datetime) -> set[str]:
    """未成功来源到一小时冷却期后才重试，避免一次超时导致每轮重采全部官网。"""
    rows = _official_runs_today(now)
    done = {source for source, status, _ in rows if status == "ok"}
    latest: dict[str, datetime] = {}
    for source, _, started_at in rows:
        if source and source not in done and (source not in latest or started_at > latest[source]):
            latest[source] = started_at
    cutoff = now.astimezone(UTC) - OFFICIAL_RETRY_COOLDOWN
    return {source for source in OFFICIAL_RAW if source not in done
            and (source not in latest or latest[source].astimezone(UTC) <= cutoff)}


def official_done_today(now: datetime) -> bool:
    """今天官网整批重采过没有：查库里的采集记录，不只看进程内存（10-12：重启一次就又整批采一遍，约 35 分钟）。"""
    done = {source for source, status, _ in _official_runs_today(now) if status == "ok"}
    return set(OFFICIAL_RAW) <= set(done)


def official_daily(echo: Callable[[str], None] = print) -> None:
    """官网每天整批重采一次（公开页面，A 档）：新出现的岗位才看得到，连续没再出现的才能判断下架，趋势才有数据。
    每个官网把全部方向词都搜一遍；没变的内容只更新"最后一次见到"的时间，不重复抽取。"""
    from polaris.collectors import queries as q
    from polaris.collectors.sites import ByteDanceCollector, XhsCareersCollector
    from polaris.collectors.tencent import TencentCollector
    from polaris.extract import run_extract

    pending = official_pending_sources(datetime.now(BEIJING))
    if not pending:
        return
    makers = {"tencent": TencentCollector, "bytedance": ByteDanceCollector, "xhs_careers": XhsCareersCollector}
    for source, make in makers.items():
        if source not in pending:
            continue
        try:
            q.ensure_seeded(source)
            n = len(q.existing(source))
            r = collect_round(source, make(), n, {"mode": "official-daily", "max_pages": 3}, echo=echo)
            out = run_extract("job", len(r["new_ids"]) + 10, r["run_id"], get_settings().llm_concurrency, OFFICIAL_RAW[source])
            echo(f"  [官网 {source}] 新写入 {len(r['new_ids'])} 条，抽取 {out['extracted']} 条，失败 {out.get('failed', 0)} 条")
        except Exception as e:  # noqa: BLE001  一个官网出错不影响别的
            echo(f"  [官网 {source}] 出错：{e}"[:200])
    try:
        # 成功快照和抽取已在各自事务中排队；不能把失败批次也当成新快照。
        maintain_knowledge(echo)
    except Exception as e:  # noqa: BLE001
        echo(f"  [官网] 刷新知识层出错：{e}"[:200])


def tick(echo: Callable[[str], None] = print, notified: set[str] | None = None) -> bool:
    """看一遍各来源，能采的跑一轮。返回这次有没有跑。"""
    maintain_knowledge(echo)
    notified = notified if notified is not None else set()
    today = datetime.now(BEIJING).date().isoformat()
    due = []
    for source in IMPLEMENTED:
        if not budget.in_window(source):
            continue
        cap, why = budget.today_cap(source)
        used = budget.used_today(source)
        if used < cap and budget.hour_left(source) > 0:
            due.append((source, cap, used, why))
    if not due:
        return False
    if not is_running():
        open_browser()
    ran = False
    with attached() as context:
        for source, cap, used, why in due:
            via = budget.policy()[source].get("via", "browser")
            if source == "boss":
                from polaris.collectors.boss import auth_status

                open_browser(["https://www.zhipin.com/"], profile="boss")
                health = auth_status()
                ok = health["authenticated"]
            else:
                ok = via != "browser" or logged_in(context, source)
            if not ok:
                # 已同步专用窗口现有凭证；仍失败则保留实际原因，不自动刷新或绕过验证。
                # 每两小时最多提醒一次，不把网络/CLI 错误一律说成用户没登录。
                key = f"{today}:{source}:login:{datetime.now(BEIJING).hour // 2}"
                if key not in notified:
                    msg = (("Boss 搜索认证失败：" + str(health.get("reason") or "请在专用窗口核实登录"))[:180] if source == "boss"
                           else f"{source} 没登录，在它的采集窗口里扫一次码就好（运行 polaris browser open）")
                    notify("北极星：需要你点一下", msg)
                    notified.add(key)
                reason = str(health.get("reason") or "搜索认证未通过") if source == "boss" else "登录预检未通过"
                echo(f"[{source}] 跳过：{reason}"[:300])
                continue
            echo(f"[{source}] {datetime.now(BEIJING):%H:%M} 今天上限 {cap}（{why}），已用 {used}")
            r = run_round(source, context, cap, used, echo)
            ran = True
            if r.get("stopped"):
                notify("北极星：采集停下了", f"{source}：{r['stopped']}"[:180])
                echo(f"[{source}] 停下：{r['stopped']}")
    return ran


def keep_awake(on: bool) -> None:
    """常驻采集开着时不让电脑自己睡着（Windows 的 SetThreadExecutionState，和播放器、下载工具用的一样，不改系统设置）。
    合上笔记本盖子、手动点睡眠仍然会睡。"""
    if sys.platform != "win32":
        return
    import ctypes

    es_continuous, es_system_required = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | (es_system_required if on else 0))


def single_instance() -> object | None:
    """同一时间只跑一个常驻采集（开机自启的和手动开的不会撞车）。拿到锁返回文件句柄（要一直留着），拿不到返回 None。
    用的是文件锁：进程退出（包括崩溃）时系统自动释放。"""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK_FILE, "a+")  # noqa: SIM115  句柄要一直开着才占着锁
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def loop(echo: Callable[[str], None] = print, idle_minutes: int = 10) -> None:
    lock = single_instance()
    if lock is None:
        echo("已经有一个常驻采集在跑了（可能是开机自启的），这个不再启动。看进度：polaris status")
        return
    notified: set[str] = set()
    morning_done: str | None = None
    official_done: str | None = None
    keep_awake(True)
    echo("常驻采集开始，电脑开着时不会自己睡着。按 Ctrl+C 停止。")
    while True:
        now = datetime.now(BEIJING)
        if now.hour * 60 + now.minute >= 8 * 60 + 30 and morning_done != now.date().isoformat():
            proactive(echo, "[早上的简报] ")  # 每天 8:30 以后第一次醒来时出一份
            morning_done = now.date().isoformat()
        if now.hour >= 9 and official_done != now.date().isoformat() and official_done_today(now):
            official_done = now.date().isoformat()  # 今天已经采过（比如进程重启了）
        if now.hour >= 9 and official_done != now.date().isoformat() and official_pending_sources(now):
            echo(f"[官网] {now:%H:%M} 检查待采官网来源")
            official_daily(echo)
            if official_done_today(datetime.now(BEIJING)):
                official_done = now.date().isoformat()
        try:
            ran = tick(echo, notified)
        except Exception as e:  # noqa: BLE001  一轮出错不让整个常驻进程退出
            echo(f"这一轮出错：{e}"[:300])
            ran = False
        if not ran:
            time.sleep(idle_minutes * 60)
