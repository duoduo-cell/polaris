"""百炼域名直连：只在系统 DNS 给出加速器假地址时改用真实地址，别的域名、别的电脑原样。"""
import socket
import struct

from polaris.llm import direct


def fake_lookup(answer):
    def lookup(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answer, port))]
    return lookup


def test_only_listed_hosts_with_fake_ip_are_rewritten(monkeypatch):
    monkeypatch.setattr(direct, "_hosts", {"dashscope.aliyuncs.com"})
    monkeypatch.setattr(direct, "_real_addresses", lambda host: ["8.152.159.24"])
    monkeypatch.setattr(direct, "_original_getaddrinfo", fake_lookup("198.18.0.150"))
    assert direct._getaddrinfo("dashscope.aliyuncs.com", 443)[0][4] == ("8.152.159.24", 443)
    assert direct._getaddrinfo("www.baidu.com", 443)[0][4] == ("198.18.0.150", 443)  # 别的域名照旧走加速器
    monkeypatch.setattr(direct, "_original_getaddrinfo", fake_lookup("39.96.198.249"))
    assert direct._getaddrinfo("dashscope.aliyuncs.com", 443)[0][4] == ("39.96.198.249", 443)  # 没有假地址：不动


def test_falls_back_to_tunnel_when_real_lookup_fails(monkeypatch):
    monkeypatch.setattr(direct, "_hosts", {"dashscope.aliyuncs.com"})
    monkeypatch.setattr(direct, "_real_addresses", lambda host: [])
    monkeypatch.setattr(direct, "_original_getaddrinfo", fake_lookup("198.18.0.150"))
    assert direct._getaddrinfo("dashscope.aliyuncs.com", 443)[0][4] == ("198.18.0.150", 443)


def test_dns_answer_with_cname_and_compression(monkeypatch):
    qid = 4660
    question = b"\x09dashscope\x08aliyuncs\x03com\x00" + struct.pack(">HH", 1, 1)
    cname = b"\xc0\x0c" + struct.pack(">HHIH", 5, 1, 30, 6) + b"\x03gtm\xc0\x0c"
    a_record = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 6, 4) + socket.inet_aton("8.152.159.24")
    response = struct.pack(">HHHHHH", qid, 0x8180, 1, 2, 0, 0) + question + cname + a_record

    class FakeSocket:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def settimeout(self, t): pass
        def bind(self, addr): self.bound = addr
        def sendto(self, packet, addr): pass
        def recv(self, n): return response

    monkeypatch.setattr(direct.random, "randrange", lambda n: qid)
    monkeypatch.setattr(direct.socket, "socket", FakeSocket)
    assert direct._dns_query("dashscope.aliyuncs.com", "223.5.5.5", "192.168.0.100") == ["8.152.159.24"]
