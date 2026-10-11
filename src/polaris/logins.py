"""登录状态：采集专用浏览器里各平台是否还登录着；掉了就弹一条桌面通知，让你扫一次码。

判断只看 cookie 的名字，不读值。依据：Boss 的登录 cookie 是 wt2、zp_at（boss-cli 的 REQUIRED_COOKIES）；
小红书是 web_session（MediaCrawler 用它判断登录）；脉脉的在你第一次登录后，对比登录前后多出来的 cookie 名确定。
登录状态存在采集浏览器自己的资料夹里（data/browser/collect，不进仓库），关掉浏览器、重启电脑都还在，
直到平台自己让它过期。浏览器是真的、页面脚本照常运行，平台的令牌会自己续上。
"""
import subprocess
from typing import Any

SITES = {"boss": "https://www.zhipin.com", "xhs": "https://www.xiaohongshu.com", "maimai": "https://maimai.cn"}
LOGIN_COOKIES = {"boss": {"wt2", "zp_at"}, "xhs": {"web_session"}, "maimai": {"u", "access_token"}}
LOGIN_PAGES = {"boss": "https://www.zhipin.com/web/user/?ka=header-login", "xhs": "https://www.xiaohongshu.com/explore",
               "maimai": "https://maimai.cn/"}


def cookie_names(context: Any, source: str) -> set[str]:
    return {c["name"] for c in context.cookies(SITES[source])}


def logged_in(context: Any, source: str) -> bool:
    names = cookie_names(context, source)
    return bool(LOGIN_COOKIES[source] & names) if source != "boss" else LOGIN_COOKIES["boss"] <= names


def notify(title: str, message: str) -> None:
    """Windows 桌面通知（系统自带的通知中心，不装别的东西）；失败就算了，日报里也会写。"""
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$n = $t.GetElementsByTagName('text');"
        f"$n.Item(0).AppendChild($t.CreateTextNode('{_ps(title)}')) > $null;"
        f"$n.Item(1).AppendChild($t.CreateTextNode('{_ps(message)}')) > $null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Polaris').Show("
        "[Windows.UI.Notifications.ToastNotification]::new($t))")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script], timeout=15, capture_output=True)
    except Exception:  # noqa: BLE001
        pass


def _ps(text: str) -> str:
    return text.replace("'", "''")[:200]
