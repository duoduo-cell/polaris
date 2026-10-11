"""常驻采集：开着就行，不用每次手动开浏览器、登录。

每隔一会儿看一遍各来源：在采集时间段内、今天的量没用完、还登录着，就跑一轮（几个查询词）；
采完马上抽取，小红书再提新查询词。被拦就停这个来源到明天并弹通知；掉登录就弹通知等你扫码，别的来源照常。
每天的量按 config/collect.toml 慢慢加（budget.py）。
"""
import sys
import time
from collections.abc import Callable
from datetime import datetime
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
    collector = FACTORIES[source](context, left, cfg)
    try:
        r = collect_round(source, collector, cfg["round_queries"],
                          {"max_calls": left, "cap": cap, "via": cfg.get("via", "browser"), "mode": "daemon"}, echo=echo)
    finally:  # 每轮结束关掉自己开的标签页
        for obj in (collector, getattr(collector, "caller", None)):
            if hasattr(obj, "close"):
                obj.close()
    if r["new_ids"]:
        try:  # 抽取或提词出错不影响继续采
            kind, raw_source = POST_SOURCES[source]
            out = run_extract(kind, len(r["new_ids"]) + 10, r["run_id"], get_settings().llm_concurrency, raw_source)
            added = discover_queries(r["new_ids"], r["run_id"]) if source == "xhs" else []
            echo(f"  [{source}] 抽取 {out['extracted']} 条" + (f"；新查询词：{'、'.join(added)}" if added else ""))
        except Exception as e:  # noqa: BLE001
            echo(f"  [{source}] 抽取或提词出错（采集继续）：{e}"[:200])
    return r


def tick(echo: Callable[[str], None] = print, notified: set[str] | None = None) -> bool:
    """看一遍各来源，能采的跑一轮。返回这次有没有跑。"""
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
                from polaris.collectors.boss import logged_in as boss_logged_in

                open_browser(["https://www.zhipin.com/"], profile="boss")
                ok = boss_logged_in()
            else:
                ok = via != "browser" or logged_in(context, source)
            if not ok:
                # Boss 的令牌二三十分钟就过期，只有页面重新加载时 Boss 自己才会生成新的；我们不替你刷新它的页面
                # （那就是在绕它的反自动化检测），所以这里只提醒：你在 Boss 窗口里点一下，就能接着采。每两小时最多提醒一次。
                key = f"{today}:{source}:login:{datetime.now(BEIJING).hour // 2}"
                if key not in notified:
                    msg = ("Boss 的令牌过期了：在 Boss 窗口里按一下 F5 或随便点一个页面，就能接着采" if source == "boss"
                           else f"{source} 没登录，在它的采集窗口里扫一次码就好（运行 polaris browser open）")
                    notify("北极星：需要你点一下", msg)
                    notified.add(key)
                echo(f"[{source}] 没登录，跳过")
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
    keep_awake(True)
    echo("常驻采集开始，电脑开着时不会自己睡着。按 Ctrl+C 停止。")
    while True:
        try:
            ran = tick(echo, notified)
        except Exception as e:  # noqa: BLE001  一轮出错不让整个常驻进程退出
            echo(f"这一轮出错：{e}"[:300])
            ran = False
        if not ran:
            time.sleep(idle_minutes * 60)
