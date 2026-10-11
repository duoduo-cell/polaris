"""同一个平台同一时间只有一个人在用：常驻采集的一轮和对话里的现查共用一把文件锁（data/locks/<来源>.lock）。

文件锁在进程退出（包括崩溃）时由系统释放，不会留下死锁。
"""
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

from polaris.config import ROOT

LOCK_DIR = ROOT / "data" / "locks"


class PlatformBusy(RuntimeError):
    pass


def _try(f) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _release(f) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def platform_lock(source: str, timeout: float = 30.0) -> Iterator[None]:
    """拿到锁才进；等到 timeout 还拿不到就抛 PlatformBusy。"""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    name = source.split(":")[0]
    f = open(LOCK_DIR / f"{name}.lock", "a+")  # noqa: SIM115  句柄要一直开着才占着锁
    deadline = time.monotonic() + timeout
    try:
        while not _try(f):
            if time.monotonic() >= deadline:
                raise PlatformBusy(f"{name} 正在被另一个任务使用（常驻采集或另一次现查）")
            time.sleep(0.5)
        try:
            yield
        finally:
            _release(f)
    finally:
        f.close()
