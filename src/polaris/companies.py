"""风向标大厂名单：读 config/big_companies.toml，判断一个公司名（或部门名、产品名）属于哪家大厂。"""
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from polaris.config import ROOT

CONFIG = ROOT / "config" / "big_companies.toml"


@dataclass(frozen=True)
class BigCompany:
    name: str
    group: str
    aliases: tuple[str, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@lru_cache
def load(path: Path = CONFIG) -> tuple[BigCompany, ...]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return tuple(BigCompany(c["name"], c.get("group", "大厂"), tuple(c.get("aliases", []))) for c in data["company"])


def _pattern(alias: str) -> re.Pattern[str]:
    # 英文别名按单词边界匹配（避免 RED 匹配到 credit）；中文直接按子串
    if re.fullmatch(r"[A-Za-z0-9 .]+", alias):
        return re.compile(rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", re.I)
    return re.compile(re.escape(alias))


@lru_cache
def _patterns() -> tuple[tuple[BigCompany, tuple[re.Pattern[str], ...]], ...]:
    return tuple((c, tuple(_pattern(a) for a in sorted(c.names, key=len, reverse=True))) for c in load())


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
