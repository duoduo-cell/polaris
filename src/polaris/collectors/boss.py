"""Boss直聘（B 档）：调本机的 boss-cli 命令行（用你本人的账号扫码登录），只用 search 和 detail。

打招呼、批量打招呼、投递、聊天这些命令在这里直接拦下，北极星不调用。
搜索时就按公司规模筛（万人以上、千人以上），拿到列表后再按大厂名单过滤，只有大厂的岗位才打开详情。
boss-cli 自己带随机延迟和限流退避；这里每轮再设调用上限，碰到限流或登录过期就停下这一轮。
"""
import json
import os
import random
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from sqlalchemy import select

from polaris.collectors.base import Query
from polaris.companies import match
from polaris.db.models import RawItem
from polaris.db.session import session_scope
from polaris.ingest import RawItemIn

READ_ONLY_COMMANDS = frozenset({"search", "detail", "status", "cities"})
CARD_KEYS = ("encryptJobId", "jobName", "brandName", "salaryDesc", "cityName", "areaDistrict", "businessDistrict",
             "jobExperience", "jobDegree", "skills", "jobLabels", "brandScaleName", "brandStageName",
             "brandIndustry", "welfareList")


class BlockedCommand(PermissionError):
    pass


class BossCallFailed(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def boss_bin() -> str:
    return os.environ.get("BOSS_BIN") or shutil.which("boss") or "boss"


def _browser_cookies() -> dict[str, str]:
    """只读专用浏览器的 cookie，不附加页面、刷新或运行页面脚本。"""
    from playwright.sync_api import sync_playwright

    from polaris.browser import cdp_url, is_running

    if not is_running("boss"):
        return {}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url("boss"), timeout=5000)
        try:
            session = browser.new_browser_cdp_session()
            cookies = session.send("Storage.getCookies").get("cookies", [])
            return {c["name"]: c["value"] for c in cookies
                    if c.get("domain", "").lstrip(".") == "zhipin.com"
                    or c.get("domain", "").endswith(".zhipin.com")}
        finally:
            browser.close()  # 仅断开 CDP，保留登录窗口


def sync_browser_credential(path: Path | None = None) -> bool:
    """修正 boss-cli 7 天缓存：仅同步完整且变化的本人浏览器凭证，原子替换。"""
    path = path or Path.home() / ".config" / "boss-cli" / "credential.json"
    # daemon 已在 collect 的同步 Playwright 上下文里，不能在同线程嵌套启动驱动。
    # 专用线程只读 Boss 浏览器级 cookie，不操作任一页面。
    with ThreadPoolExecutor(max_workers=1) as pool:
        cookies = pool.submit(_browser_cookies).result()
    if not all(cookies.get(k) for k in ("wt2", "zp_at", "wbg", "__zp_stoken__")):
        return False
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        old = {}
    if old.get("cookies") == cookies:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".credential-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"cookies": cookies, "saved_at": time.time()}, f)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)
    return True


def run_boss(args: list[str], timeout: float = 120) -> dict[str, Any]:
    """调一个只读命令，返回 data；出错时抛 BossCallFailed（code 是 not_authenticated、rate_limited 等）。"""
    if not args or args[0] not in READ_ONLY_COMMANDS:
        raise BlockedCommand(f"{args[:1]} 不是只读命令，北极星不调用它")
    from polaris.browser import cdp_url

    try:
        sync_browser_credential()
    except Exception:  # noqa: BLE001  窗口暂不可读时允许现有凭证正常报错，不能输出 cookie
        pass
    # 同步在上面显式完成；不能依赖 boss-cli 的 7 天缓存自动失效。
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "BOSS_CDP_URL": cdp_url("boss")}
    p = subprocess.run([boss_bin(), *args, "--json"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=timeout)
    try:
        envelope = json.loads(p.stdout)
    except json.JSONDecodeError:
        tail = (p.stderr or p.stdout or "").strip()[-200:]
        raise BossCallFailed("not_authenticated" if "未登录" in tail else "bad_output", tail) from None
    if not envelope.get("ok"):
        err = envelope.get("error") or {}
        raise BossCallFailed(err.get("code", "api_error"), str(err.get("message", ""))[:200])
    return envelope.get("data") or {}


def auth_status(timeout: float = 60) -> dict[str, Any]:
    """同步专用窗口现有凭证，并保留 CLI 的实际搜索认证结果。

    status 的输出没有其他命令那层 {ok, data}，直接是 {"authenticated": ..., "search_authenticated": ...}（10-11 实测）。
    """
    from polaris.browser import cdp_url

    synced = False
    sync_error = None
    try:
        synced = sync_browser_credential()
    except Exception as e:  # noqa: BLE001
        sync_error = f"浏览器凭证同步失败（{type(e).__name__}）"
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "BOSS_CDP_URL": cdp_url("boss")}
    try:
        p = subprocess.run([boss_bin(), "status", "--json"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=timeout)
        data = json.loads(p.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        return {"authenticated": False, "reason": f"CLI 状态检查失败（{type(e).__name__}）"}
    data = data.get("data", data) if isinstance(data, dict) else {}
    if not isinstance(data, dict):
        data = {}
    ok = data.get("search_authenticated", data.get("authenticated")) is True
    return {"authenticated": ok, "synced": synced,
            "reason": None if ok else (sync_error or data.get("reason") or "搜索认证未通过")}


def logged_in(timeout: float = 60) -> bool:
    return auth_status(timeout)["authenticated"]


def slim_card(job: dict[str, Any]) -> dict[str, Any]:
    """搜索结果里只留岗位和公司的字段，招聘者的姓名、头像、职务都不留。"""
    return {k: job.get(k) for k in CARD_KEYS if job.get(k) not in (None, "", [])}


def slim_detail(detail: dict[str, Any]) -> dict[str, Any]:
    info = detail.get("jobInfo") or {}
    brand = detail.get("brandComInfo") or {}
    keep_info = ("jobName", "positionName", "locationName", "address", "experienceName", "degreeName", "salaryDesc",
                 "postDescription", "showSkills", "jobStatusDesc")
    keep_brand = ("brandName", "scaleName", "stageName", "industryName")
    return {"jobInfo": {k: info.get(k) for k in keep_info if info.get(k)},
            "brandComInfo": {k: brand.get(k) for k in keep_brand if brand.get(k)}}


def to_text(card: dict[str, Any], detail: dict[str, Any]) -> str:
    info, brand = detail.get("jobInfo") or {}, detail.get("brandComInfo") or {}
    skills = card.get("skills") or info.get("showSkills") or []
    lines = [info.get("jobName") or card.get("jobName") or "",
             f"公司：{brand.get('brandName') or card.get('brandName') or ''}",
             f"地点：{info.get('locationName') or card.get('cityName') or ''} {card.get('areaDistrict') or ''}".strip(),
             f"薪资：{info.get('salaryDesc') or card.get('salaryDesc') or ''}",
             f"经验：{info.get('experienceName') or card.get('jobExperience') or ''}  "
             f"学历：{info.get('degreeName') or card.get('jobDegree') or ''}",
             f"公司规模：{brand.get('scaleName') or card.get('brandScaleName') or ''}  "
             f"融资：{brand.get('stageName') or card.get('brandStageName') or ''}  "
             f"行业：{brand.get('industryName') or card.get('brandIndustry') or ''}"]
    if skills:
        lines.append("技能：" + "、".join(skills))
    lines += ["职位描述：", (info.get("postDescription") or "").strip()]
    return "\n".join(lines)


def known_job_ids() -> set[str]:
    with session_scope() as s:
        rows = s.execute(select(RawItem.external_id).where(RawItem.source == "boss")).scalars().all()
    return {r for r in rows if r}


@dataclass
class BossStats:
    queries: int = 0
    searched: int = 0
    cards_seen: int = 0
    skipped_small: int = 0
    skipped_known: int = 0
    opened: int = 0
    calls: int = 0
    failures: int = 0
    rate_limited: int = 0
    seconds: float = 0.0
    stopped: str | None = None
    errors: list[str] = field(default_factory=list)


class BossCollector:
    """横向：查询词 × 城市 × 公司规模，多页搜索；过滤：品牌名在大厂名单里才留下；纵向：留下的打开详情读职位描述。"""

    name: ClassVar[str] = "boss"
    tier: ClassVar[Literal["A", "B"]] = "B"
    mode: ClassVar[Literal["search", "site"]] = "search"

    def __init__(self, cities: tuple[str, ...] = ("北京", "上海", "深圳", "杭州"),
                 scales: tuple[str, ...] = ("10000人以上", "1000-9999人"), job_type: str | None = "实习",
                 max_calls: int = 40, pause: tuple[float, float] = (2.0, 5.0), known: set[str] | None = None,
                 runner: Callable[[list[str]], dict[str, Any]] = run_boss):
        self.cities, self.scales, self.job_type = cities, scales, job_type
        self.max_calls, self.pause = max_calls, pause
        self.known = known if known is not None else known_job_ids()
        self.runner = runner
        self.per_query_calls: int | None = None
        self.stats = BossStats()
        self._query_start = 0

    def _budget_left(self) -> bool:
        if self.stats.stopped or self.stats.calls >= self.max_calls:
            return False
        return self.per_query_calls is None or self.stats.calls - self._query_start < self.per_query_calls

    def _call(self, args: list[str]) -> dict[str, Any] | None:
        if not self._budget_left():
            return None
        self.stats.calls += 1
        start = time.perf_counter()
        try:
            return self.runner(args)
        except BossCallFailed as e:
            self.stats.failures += 1
            self.stats.errors.append(str(e)[:200])
            if e.code == "rate_limited":
                self.stats.rate_limited += 1
                self.stats.stopped = "rate_limited"  # 被限流就停下这一轮，不硬冲
            elif e.code == "not_authenticated":
                self.stats.stopped = "not_authenticated"
            return None
        except Exception as e:  # noqa: BLE001  单次失败不影响整轮
            self.stats.failures += 1
            self.stats.errors.append(f"{args[0]}: {e}"[:200])
            return None
        finally:
            self.stats.seconds += time.perf_counter() - start
            if self.pause[1] > 0:
                time.sleep(random.uniform(*self.pause))

    def _search_args(self, keyword: str, city: str, scale: str, page: int) -> list[str]:
        args = ["search", keyword, "--city", city, "--scale", scale, "-p", str(page)]
        return args + (["--job-type", self.job_type] if self.job_type else [])

    def collect(self, query: Query) -> Iterator[RawItemIn]:
        for keyword in query.keywords:
            if self.stats.stopped or self.stats.calls >= self.max_calls:
                return
            self.stats.queries += 1
            self._query_start = self.stats.calls
            searched = False
            for city in self.cities:
                for scale in self.scales:
                    for page in range(1, query.max_pages + 1):
                        data = self._call(self._search_args(keyword, city, scale, page))
                        if data is None:
                            break
                        searched = True
                        jobs = data.get("jobList") or []
                        yield from self._filter_and_open(jobs, keyword)
                        if not data.get("hasMore") or not jobs:
                            break
            self.stats.searched += searched

    def _filter_and_open(self, jobs: list[dict[str, Any]], keyword: str) -> Iterator[RawItemIn]:
        for job in jobs:
            self.stats.cards_seen += 1
            big = match(job.get("brandName"))
            if not big:
                self.stats.skipped_small += 1
                continue
            jid, sid = job.get("encryptJobId"), job.get("securityId")
            if not jid or not sid:
                continue
            if jid in self.known:
                self.stats.skipped_known += 1
                continue
            detail = self._call(["detail", sid])
            if detail is None:
                if self.stats.stopped:
                    return
                continue
            self.stats.opened += 1
            self.known.add(jid)
            card = slim_card(job)
            yield RawItemIn(source="boss", tier="B", kind="job", url=f"https://www.zhipin.com/job_detail/{jid}.html",
                            external_id=jid, query=keyword, raw_text=to_text(card, detail),
                            raw_json={"card": card, "detail": slim_detail(detail), "big_company": big.name},
                            fetched_at=datetime.now(UTC), fetch_method="cli", collector_version="boss-1")
