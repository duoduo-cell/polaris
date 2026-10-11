"""采集专用浏览器：正常启动你电脑上的 Edge（单独的资料夹，不碰你日常用的），你在里面登录一次；之后一直有效。

两个资料夹：
- collect（端口 9222）：小红书、脉脉。采集器通过本机调试端口接上它，像人一样浏览，读页面自己的数据。
- boss（端口 9223）：只放 Boss。Boss 会识别被程序接管的页面并跳成空白（10-11 实测），所以我们的程序从不接管这个窗口的页面；
  boss-cli 只在需要时向它要一次 Boss 的 cookie（Network.getAllCookies，不在页面里运行任何脚本），拿去调 Boss 的搜索和详情。
为什么这样做（DATASET.md 第 4 节）：浏览器、账号、IP 都是真的，没有任何伪造；平台页面自己的脚本算签名和令牌，
我们不逆向、不"补环境"、不隐藏自动化。调试端口只绑本机 127.0.0.1。
"""
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

from polaris.config import ROOT

PROFILES = {"collect": (ROOT / "data" / "browser" / "collect", 9222),
            "boss": (ROOT / "data" / "browser" / "boss", 9223)}
PROFILE_DIR, CDP_PORT = PROFILES["collect"]
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
EDGE_CANDIDATES = (Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                   Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"))


def cdp_url(profile: str = "collect") -> str:
    return f"http://127.0.0.1:{PROFILES[profile][1]}"


def edge_path() -> str:
    for p in EDGE_CANDIDATES:
        if p.exists():
            return str(p)
    found = shutil.which("msedge")
    if not found:
        raise FileNotFoundError("没找到 Edge（msedge.exe）")
    return found


def is_running(profile: str = "collect") -> bool:
    try:
        return httpx.get(f"{cdp_url(profile)}/json/version", timeout=2, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


def open_browser(urls: list[str] | None = None, profile: str = "collect") -> bool:
    """启动某个采集专用的 Edge（已经开着就不重复开）。返回这次是否新启动。"""
    if is_running(profile):
        return False
    folder, port = PROFILES[profile]
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([edge_path(), f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
                      f"--user-data-dir={folder}", "--no-first-run", "--no-default-browser-check", *(urls or [])],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        if is_running(profile):
            return True
        time.sleep(0.5)
    raise RuntimeError("Edge 启动了但调试端口没响应")


@contextmanager
def attached() -> Iterator[object]:
    """接上 collect 浏览器，返回它的上下文（带着你的登录状态）。断开时不关浏览器。Boss 的浏览器不走这里。"""
    if not is_running("collect"):
        raise RuntimeError("采集浏览器没开。先运行 polaris browser open，在窗口里登录")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        try:
            yield browser.contexts[0]
        finally:
            browser.close()  # 对接上的浏览器，close 只断开连接，不关窗口
