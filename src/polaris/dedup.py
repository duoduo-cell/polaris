"""近似重复指纹：64 位 SimHash，按字符三元组算。

用来找"编号不同、内容几乎一样"的条目：小红书上被多人搬运的同一条招聘、官网上同一岗位挂在多个城市。
方法来自 Manku 等，Detecting Near-Duplicates for Web Crawling（WWW 2007）；论文里网页用的阈值是 3，
但小红书笔记短，阈值按我们的数据标定（2026-10-11，57 条笔记）：搬运帖之间是 7 到 8，最近的两条无关笔记是 19，取 10。
官网岗位不能只看它：同一部门不同岗位（研发、算法）常共用一段 JD，距离只有 0 到 2，是否同一岗位要 M4 结合岗位名、公司、城市判断。
"""
import hashlib
import re

NOISE = re.compile(r"#[^#\s]*\[话题\]#|\[[^\]]{1,6}[RH]\]|\s+")


def _shingles(text: str, n: int = 3) -> list[str]:
    t = NOISE.sub("", text)[:2000]
    return [t[i:i + n] for i in range(max(1, len(t) - n + 1))]


def simhash64(text: str) -> int:
    """返回有符号 64 位整数（Postgres BIGINT 能存）。"""
    weights = [0] * 64
    for sh in _shingles(text):
        h = int.from_bytes(hashlib.blake2b(sh.encode("utf-8"), digest_size=8).digest(), "big")
        for i in range(64):
            weights[i] += 1 if h >> i & 1 else -1
    value = sum(1 << i for i in range(64) if weights[i] > 0)
    return value - (1 << 64) if value >= 1 << 63 else value


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & ((1 << 64) - 1)).bit_count()


def near_duplicate(a: int, b: int, threshold: int = 10) -> bool:
    return hamming(a, b) <= threshold


def shingle_set(text: str, n: int = 3) -> frozenset[str]:
    """字符三元组集合，算 Jaccard 相似度用（Broder 1997 的 resemblance）。不截断：同名岗位的区别常在后半段的职责里。"""
    t = NOISE.sub("", text)
    return frozenset(t[i:i + n] for i in range(len(t) - n + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0
