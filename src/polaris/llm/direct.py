"""百炼域名直连：本机加速器（TUN + fake-ip）接管全部流量时，只让模型服务的域名绕开隧道。

10-24 整链实测：经“alijiasu”隧道连百炼，TLS 握手 1~10 秒、约一半到七成卡死；同一时刻从 WLAN 网卡直连真实地址
6/6 成功、每次约 0.2 秒。用户允许“仅百炼域名直连”，不改系统代理或加速器设置。

做法（只在系统 DNS 给出 198.18.0.0/15 假地址时生效，别的电脑上什么都不做）：
1. 向阿里公共 DNS 查真实地址，查询从默认路由网卡的地址发出，不进隧道；
2. 连这些真实地址的 socket 先绑定到默认路由网卡的地址，再 connect（Windows 按源地址选网卡出去）。
任何一步失败都退回原来的路径（走隧道），不让模型调用因为直连本身报错。
"""
import ipaddress
import os
import random
import socket
import struct
import subprocess
import threading
import time

FAKE_IP = ipaddress.ip_network("198.18.0.0/15")
DNS_SERVERS = ("223.5.5.5", "223.6.6.6")
CACHE_SECONDS = 300

_lock = threading.Lock()
_hosts: set[str] = set()
_real: dict[str, tuple[float, list[str]]] = {}  # 域名 -> (查询时间, 真实地址)
_direct_ips: set[str] = set()
_local: tuple[float, str | None] = (0.0, None)
_original_getaddrinfo = socket.getaddrinfo
_original_connect = socket.socket.connect


def default_route_address() -> str | None:
    """默认路由（0.0.0.0/0）所在网卡的本机地址。隧道用更细的路由盖住它，所以不能用 connect 探测。"""
    global _local
    now = time.monotonic()
    if now - _local[0] < CACHE_SECONDS:
        return _local[1]
    address = None
    if os.name == "nt":
        try:
            out = subprocess.run(["route", "print", "-4", "0.0.0.0"], capture_output=True, timeout=5,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                 ).stdout.decode("ascii", "replace")  # 中文系统输出是 GBK，只需要其中的数字
            routes = []
            for line in out.splitlines():
                parts = line.split()
                if len(parts) == 5 and parts[0] == parts[1] == "0.0.0.0" and parts[4].isdigit():
                    routes.append((int(parts[4]), parts[3]))
            address = min(routes)[1] if routes else None
        except (OSError, subprocess.SubprocessError, ValueError):
            address = None
    _local = (now, address)
    return address


def _dns_query(name: str, server: str, local: str) -> list[str]:
    """最小的 A 记录查询（UDP 53），从 local 地址发出。"""
    qid = random.randrange(65536)
    question = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00" + struct.pack(">HH", 1, 1)
    packet = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + question
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(3)
        s.bind((local, 0))
        s.sendto(packet, (server, 53))
        data = s.recv(2048)
    rid, _, qd, an = struct.unpack(">HHHH", data[:8])
    if rid != qid:
        return []
    pos = 12
    for _ in range(qd):  # 跳过问题区
        while data[pos]:
            pos += 1 + data[pos]
        pos += 5
    out = []
    for _ in range(an):
        pos = pos + 2 if data[pos] & 0xC0 == 0xC0 else _skip_name(data, pos)
        rtype, _, _, length = struct.unpack(">HHIH", data[pos:pos + 10])
        pos += 10
        if rtype == 1 and length == 4:
            out.append(socket.inet_ntoa(data[pos:pos + 4]))
        pos += length
    return out


def _skip_name(data: bytes, pos: int) -> int:
    while data[pos]:
        if data[pos] & 0xC0 == 0xC0:
            return pos + 2
        pos += 1 + data[pos]
    return pos + 1


def _real_addresses(host: str) -> list[str]:
    now = time.monotonic()
    with _lock:
        cached = _real.get(host)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    local = default_route_address()
    if not local:
        return []
    for server in DNS_SERVERS:
        try:
            ips = [ip for ip in _dns_query(host, server, local) if ipaddress.ip_address(ip) not in FAKE_IP]
        except (OSError, struct.error, IndexError):
            continue
        if ips:
            with _lock:
                _real[host] = (now, ips)
                _direct_ips.update(ips)
            return ips
    return []


def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002  与 socket.getaddrinfo 同签名
    results = _original_getaddrinfo(host, port, family, type, proto, flags)
    if not isinstance(host, str) or host.lower() not in _hosts:
        return results
    if not results or not all(ipaddress.ip_address(r[4][0]) in FAKE_IP for r in results if r[0] == socket.AF_INET):
        return results
    ips = _real_addresses(host.lower())
    if not ips:
        return results
    stype = type or socket.SOCK_STREAM
    return [(socket.AF_INET, stype, proto or socket.IPPROTO_TCP, "", (ip, port)) for ip in ips]


def _connect(self, address):
    if (isinstance(address, tuple) and address and address[0] in _direct_ips
            and self.family == socket.AF_INET and (local := default_route_address())):
        try:
            bound = self.getsockname()[1] != 0
        except OSError:  # Windows 上没绑定的 socket 取地址会报错
            bound = False
        if not bound:
            try:
                self.bind((local, 0))
            except OSError:
                pass  # 网卡变了：照原样连，失败由上层重试
    return _original_connect(self, address)


def install(hosts: str) -> None:
    """在本进程里对这些域名启用直连（逗号分隔，空字符串表示不启用）。可重复调用。"""
    names = {h.strip().lower() for h in hosts.split(",") if h.strip()}
    if not names:
        return
    with _lock:
        _hosts.update(names)
        if socket.getaddrinfo is not _getaddrinfo:
            socket.getaddrinfo = _getaddrinfo
            socket.socket.connect = _connect
