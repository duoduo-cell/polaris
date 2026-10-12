"""手机模式：让手机实时用上电脑里跑着的北极星。

做法参照 Open WebUI（网页做成可安装的 PWA，手机经局域网或 Tailscale 连回电脑）和 Happy（扫电脑屏幕上的二维码配对）：
- 网页服务平时只听本机；`scripts/start.ps1 -Phone` 才对局域网开放，并且每台设备要先配对；
- 配对码存在 data/phone/pair-code（不进仓库），重启不用重新配对，`--new` 换码后旧设备要重扫；
- 二维码里是 http://<这台电脑的地址>:3000/pair?code=…，扫了就记住这台手机 90 天。

agent 本身还在电脑上跑：手机锁屏、切走时任务照常进行，回来马上刷新到最新进度。
"""
import ipaddress
import json
import secrets
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from polaris.config import ROOT

DIR = ROOT / "data" / "phone"
CODE_FILE = DIR / "pair-code"
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 去掉容易看错的 0 O 1 I L


def pair_code(new: bool = False) -> str:
    """读出配对码，没有（或要换）就生成一个：8 位，显示成 ABCD-EFGH，约 39 位熵；网页那边错 8 次锁 10 分钟。"""
    if not new and CODE_FILE.exists():
        code = CODE_FILE.read_text(encoding="utf-8").strip()
        if len(code.replace("-", "")) == 8:
            return code
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    code = f"{raw[:4]}-{raw[4:]}"
    DIR.mkdir(parents=True, exist_ok=True)
    CODE_FILE.write_text(code, encoding="utf-8")
    return code


def _usable(ip: str) -> bool:
    """手机能连到的局域网地址：私有网段，排除代理或加速器的虚拟网卡（198.18/15）和 Tailscale、运营商 NAT（100.64/10）。"""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local
            and a not in ipaddress.ip_network("198.18.0.0/15") and a not in ipaddress.ip_network("100.64.0.0/10"))


def _gateway_ips() -> list[str]:
    """Windows：有默认网关的网卡的地址。VMware、WSL 这类虚拟网卡没有网关，手机也连不到，用这个排除掉（10-12 实测
    一台电脑上有 3 个 VMware 的 192.168.x.1 和一个加速器的 198.18.0.1，只有 WLAN 那个是对的）。"""
    if sys.platform != "win32":
        return []
    cmd = ("Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | "
           "ForEach-Object { $_.IPv4Address.IPAddress }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def lan_ip() -> str | None:
    """这台电脑在局域网里的地址。先看有默认网关的网卡；不是 Windows 或没查到时，看系统默认出口
    （向外"连"一个 UDP 地址只是让系统选出口网卡，不会真的发包）。"""
    for ip in _gateway_ips():
        if _usable(ip):
            return ip
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("223.5.5.5", 53))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return ip if _usable(ip) else None


def tailscale() -> tuple[str | None, str | None]:
    """装了 Tailscale 且已登录时：(100.x 地址, MagicDNS 名字)。没装或没连上就都是 None。"""
    exe = shutil.which("tailscale")
    if not exe:
        return None, None
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=5, encoding="utf-8")
        me = json.loads(out.stdout or "{}").get("Self") or {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None, None
    ip = next((a for a in me.get("TailscaleIPs") or [] if "." in a), None)
    name = (me.get("DNSName") or "").rstrip(".") or None
    return ip, name


@dataclass
class Phone:
    code: str
    port: int
    lan: str | None
    tailnet_ip: str | None
    tailnet_name: str | None
    via: str = "lan"  # tailscale：网页只听本机，由 tailscale serve 转成 HTTPS；lan：网页对局域网开放
    urls: list[dict[str, str]] = field(default_factory=list)

    @property
    def origins(self) -> str:
        """给网页开发服务放行的来源（next.config 的 allowedDevOrigins；*.ts.net 已经固定放行）。"""
        return ",".join(h for h in (self.lan, self.tailnet_ip) if h)


def info(port: int = 3000, new: bool = False) -> Phone:
    """装了 Tailscale 并登录时只走它：手机在任何网络都能连，网页仍只听本机，局域网里的人碰不到；
    没装时退回"同一个 Wi-Fi"，网页对局域网开放（仍要配对）。"""
    code = pair_code(new)
    ts_ip, ts_name = tailscale()
    p = Phone(code=code, port=port, lan=lan_ip(), tailnet_ip=ts_ip, tailnet_name=ts_name)
    if ts_name:
        p.via = "tailscale"
        p.urls.append({"label": "Tailscale（手机在哪个网络都能用）", "url": f"https://{ts_name}/pair?code={code}"})
        return p
    if p.lan:
        p.urls.append({"label": "同一个 Wi-Fi", "url": f"http://{p.lan}:{port}/pair?code={code}"})
    if ts_ip:  # 装了但没开 MagicDNS：没有 HTTPS 名字，只能直连 100.x 地址
        p.urls.append({"label": "Tailscale", "url": f"http://{ts_ip}:{port}/pair?code={code}"})
    return p


def qr_images(p: Phone) -> list[Path]:
    """每个地址一张二维码图片（data/phone/，不进仓库）。"""
    import segno

    DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, u in enumerate(p.urls):
        path = DIR / f"qr-{i + 1}.png"
        segno.make(u["url"], error="m").save(str(path), scale=10, border=3)
        paths.append(path)
    return paths
