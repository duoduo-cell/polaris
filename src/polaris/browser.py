"""采集专用浏览器：正常启动你电脑上的 Edge（单独的资料夹，不碰你日常用的），你在里面登录一次；采集器通过本机调试端口接上它。

为什么这样做（DATASET.md 第 4 节）：
- 浏览器、指纹、账号、IP 都是真的，没有任何伪造；
- 平台页面自己的脚本算签名和反爬令牌，我们不逆向、不"补环境"；
- 采集器只做一个人会做的事：打开搜索页、往下翻、点开详情，读页面自己加载的数据。
做法参照 MediaCrawler 的 CDP 模式和 boss-cli README 里 Windows 下的说明。调试端口只绑本机 127.0.0.1。
"""
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

from polaris.config import ROOT

PROFILE_DIR = ROOT / "data" / "browser" / "collect"
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
EDGE_CANDIDATES = (Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                   Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"))


def edge_path() -> str:
    for p in EDGE_CANDIDATES:
        if p.exists():
            return str(p)
    found = shutil.which("msedge")
    if not found:
        raise FileNotFoundError("没找到 Edge（msedge.exe）")
    return found


def is_running() -> bool:
    try:
        return httpx.get(f"{CDP_URL}/json/version", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def open_browser(urls: list[str] | None = None) -> bool:
    """启动采集专用的 Edge（已经开着就不重复开）。返回这次是否新启动。"""
    if is_running():
        return False
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([edge_path(), f"--remote-debugging-port={CDP_PORT}", "--remote-debugging-address=127.0.0.1",
                      f"--user-data-dir={PROFILE_DIR}", "--no-first-run", "--no-default-browser-check", *(urls or [])],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        if is_running():
            return True
        time.sleep(0.5)
    raise RuntimeError("Edge 启动了但调试端口没响应")


@contextmanager
def attached() -> Iterator[object]:
    """接上已经开着的采集浏览器，返回它的上下文（带着你的登录状态）。断开时不关浏览器。"""
    if not is_running():
        raise RuntimeError("采集浏览器没开。先运行 polaris browser open，在窗口里登录")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        try:
            yield browser.contexts[0]
        finally:
            browser.close()  # 对接上的浏览器，close 只断开连接，不关窗口
