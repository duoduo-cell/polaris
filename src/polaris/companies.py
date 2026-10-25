"""风向标大厂名单：读 config/big_companies.toml，判断一个公司名（或部门名、产品名）属于哪家大厂。"""
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from polaris.config import ROOT, get_settings

CONFIG = ROOT / "config" / "big_companies.toml"


@dataclass(frozen=True)
class BigCompany:
    name: str
    group: str
    aliases: tuple[str, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


# The synthetic demo labels its invented jobs with a fictional company (the hackathon video may not show real trademarks).
# Only demo mode knows it, so the personal instance's list stays exactly what the config says.
DEMO_COMPANY = BigCompany("合成科技", "合成演示", ())


@lru_cache
def _listed(path: Path = CONFIG) -> tuple[BigCompany, ...]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return tuple(BigCompany(c["name"], c.get("group", "大厂"), tuple(c.get("aliases", []))) for c in data["company"])


def load(path: Path = CONFIG) -> tuple[BigCompany, ...]:
    return _listed(path) + (DEMO_COMPANY,) if get_settings().demo_mode else _listed(path)


def _pattern(alias: str) -> re.Pattern[str]:
    # 英文别名按单词边界匹配（避免 RED 匹配到 credit）；中文直接按子串
    if re.fullmatch(r"[A-Za-z0-9 .]+", alias):
        return re.compile(rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", re.I)
    return re.compile(re.escape(alias))


def _patterns() -> tuple[tuple[BigCompany, tuple[re.Pattern[str], ...]], ...]:
    return _compiled(get_settings().demo_mode)


@lru_cache
def _compiled(demo: bool) -> tuple[tuple[BigCompany, tuple[re.Pattern[str], ...]], ...]:
    companies = _listed() + ((DEMO_COMPANY,) if demo else ())
    return tuple((c, tuple(_pattern(a) for a in sorted(c.names, key=len, reverse=True))) for c in companies)


def match(text: str | None) -> BigCompany | None:
    """公司名、部门名或产品名属于哪家大厂。同一家的各种叫法（字节、ByteDance、抖音……）都归到这家。"""
    if not text:
        return None
    for company, patterns in _patterns():
        if any(p.search(text) for p in patterns):
            return company
    return None


def is_big(text: str | None) -> bool:
    return match(text) is not None
