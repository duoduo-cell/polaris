"""每天的采集量：从下往上找安全的量（慢启动、出警告就减半、被拦就冷却一天）。规则写在 config/collect.toml。

思路和网络里的拥塞控制一样：没出问题就慢慢加，出了问题马上退。不去试探平台的上限——
处罚是逐级加重的（先验证码，再短时间封号，再永久封号；见 DATASET.md 第 4 节的 issue），试到上限就已经伤了账号。
每一轮的上限记在 runs.params["cap"] 里，第二天据此算新的上限，整条决策链可追溯。
"""
import tomllib
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import select

from polaris.config import ROOT
from polaris.db.models import Run
from polaris.db.session import session_scope
from polaris.report import BEIJING, day_range

CONFIG = ROOT / "config" / "collect.toml"
# 被拦才罚（冷却、减半）；登录过期、令牌过期不是被拦，不罚，只等你重新登录或刷新一下
HARD_WORDS = ("疑似被拦", "验证", "频繁", "rate_limited")
LOGIN_WORDS = ("not_authenticated", "登录", "令牌", "stoken")


@lru_cache
def policy() -> dict[str, dict[str, Any]]:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def _runs(source: str, start: datetime, end: datetime) -> list[tuple[dict, dict, datetime]]:
    with session_scope() as s:
        rows = s.execute(select(Run.params, Run.stats, Run.started_at).where(
            Run.kind == "collect", Run.started_at >= start, Run.started_at < end)).all()
    return [(p or {}, st or {}, t) for p, st, t in rows if (p or {}).get("source") == source]


def day_summary(source: str, day: datetime) -> dict[str, Any]:
    start, end = day_range(day)
    runs = _runs(source, start, end)
    calls = sum(st.get("calls", 0) or 0 for _, st, _ in runs)
    failures = sum(st.get("failures", 0) or 0 for _, st, _ in runs)
    stops = [(st["stopped"], t) for _, st, t in runs if st.get("stopped")]
    hard = [(s, t) for s, t in stops if any(w in str(s) for w in HARD_WORDS) and not any(w in str(s) for w in LOGIN_WORDS)]
    stops = [(s, t) for s, t in stops if not any(w in str(s) for w in LOGIN_WORDS)]
    caps = [p["cap"] for p, _, _ in runs if p.get("cap")]
    return {"calls": calls, "failures": failures, "hard": hard, "soft": bool(stops and not hard) or
            (calls >= 20 and failures / calls > 0.2), "cap": caps[-1] if caps else None}


def today_cap(source: str, now: datetime | None = None) -> tuple[int, str]:
    """返回（今天的上限，理由）。"""
    cfg = policy()[source]
    now = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    today = day_summary(source, now)
    if today["hard"]:
        return 0, f"今天已被拦：{today['hard'][-1][0]}，冷却到明天"
    for back in range(1, 8):  # 找最近一个采过的日子
        prev = day_summary(source, now - timedelta(days=back))
        if prev["calls"] or prev["hard"]:
            break
    else:
        return cfg["start"], "第一次（或一周没采），从起始量开始"
    base = prev["cap"] or cfg["start"]
    if prev["hard"]:
        stopped_at = prev["hard"][-1][1]
        if now - stopped_at.astimezone(BEIJING) < timedelta(hours=24):
            return 0, "昨天被拦，冷却满 24 小时再采"
        return max(10, int(base * 0.5)), "昨天被拦，减半"
    if prev["soft"]:
        return max(10, int(base * 0.5)), "昨天有警告（失败多或连续搜不到），减半"
    if prev["calls"] >= 0.8 * base:
        return min(cfg["max"], int(base * cfg["growth"])), f"昨天顺利用满，加到 {cfg['growth']} 倍"
    return base, "昨天没用满，保持"


def used_today(source: str, now: datetime | None = None) -> int:
    return day_summary(source, now or datetime.now(BEIJING))["calls"]


def in_window(source: str, now: datetime | None = None) -> bool:
    start, end = policy()[source]["window"]
    t = (now or datetime.now(BEIJING)).astimezone(BEIJING).strftime("%H:%M")
    return start <= t < end
