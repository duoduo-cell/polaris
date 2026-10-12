"""手机模式：配对码能复用也能换、局域网地址挑得对（10-12 实测：VMware 虚拟网卡和加速器的 198.18.0.1 都不能要）。"""
import re

from polaris import phone


def test_pair_code_is_kept_between_runs_and_can_be_rotated(tmp_path, monkeypatch):
    monkeypatch.setattr(phone, "DIR", tmp_path)
    monkeypatch.setattr(phone, "CODE_FILE", tmp_path / "pair-code")
    first = phone.pair_code()
    assert re.fullmatch(r"[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}", first)  # 没有 0 O 1 I L
    assert phone.pair_code() == first  # 重启不用重新配对
    assert phone.pair_code(new=True) != first


def test_only_reachable_lan_addresses_are_used():
    assert phone._usable("192.168.162.180") and phone._usable("10.0.0.5") and phone._usable("172.20.1.2")
    assert not phone._usable("198.18.0.1")  # 代理、加速器的虚拟网卡
    assert not phone._usable("100.101.102.103")  # Tailscale 自己的地址另外处理
    assert not phone._usable("127.0.0.1") and not phone._usable("169.254.14.117") and not phone._usable("8.8.8.8")


def test_lan_ip_prefers_the_adapter_with_a_gateway(monkeypatch):
    monkeypatch.setattr(phone, "_gateway_ips", lambda: ["198.18.0.1", "192.168.162.180"])
    assert phone.lan_ip() == "192.168.162.180"


def test_urls_for_wifi_and_tailscale(tmp_path, monkeypatch):
    monkeypatch.setattr(phone, "DIR", tmp_path)
    monkeypatch.setattr(phone, "CODE_FILE", tmp_path / "pair-code")
    monkeypatch.setattr(phone, "lan_ip", lambda: "192.168.1.5")
    monkeypatch.setattr(phone, "tailscale", lambda: ("100.64.0.7", "pc.tail1234.ts.net"))
    p = phone.info(port=3000)
    # 有 Tailscale 就只走它：网页仍只听本机，不对局域网开放
    assert p.via == "tailscale" and [u["url"] for u in p.urls] == [f"https://pc.tail1234.ts.net/pair?code={p.code}"]
    assert p.origins == "192.168.1.5,100.64.0.7"
    paths = phone.qr_images(p)
    assert len(paths) == 1 and paths[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    monkeypatch.setattr(phone, "tailscale", lambda: (None, None))
    lan = phone.info(port=3000)
    assert lan.via == "lan" and [u["url"] for u in lan.urls] == [f"http://192.168.1.5:3000/pair?code={lan.code}"]
    monkeypatch.setattr(phone, "lan_ip", lambda: None)
    monkeypatch.setattr(phone, "tailscale", lambda: (None, None))
    assert phone.info().urls == []  # 没联网：start.ps1 会提示先连 Wi-Fi
