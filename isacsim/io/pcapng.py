"""最小 pcapng 写入器：把 V2X 报文封装为 Ethernet/IPv4/UDP 抓包。

不依赖 scapy；用于导出与现有 5G-A-NIDD 原型同构的 PCAPNG。
链路类型：Ethernet(1)，时间精度纳秒(if_tsresol=9)。
"""
from __future__ import annotations

import struct
from pathlib import Path


def _pad4(data: bytes) -> bytes:
    return data + b"\x00" * ((-len(data)) % 4)


def _block(btype: int, body: bytes) -> bytes:
    total = 12 + len(body)
    return struct.pack("<II", btype, total) + body + struct.pack("<I", total)


def _opt(code: int, value: bytes) -> bytes:
    return struct.pack("<HH", code, len(value)) + _pad4(value)


def _section_header() -> bytes:
    body = struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)
    body += _opt(0, b"")  # opt_endofopt
    return _block(0x0A0D0D0A, body)


def _interface(if_name: str = "pc5", snaplen: int = 65535) -> bytes:
    body = struct.pack("<HHI", 1, 0, snaplen)  # LINKTYPE_ETHERNET
    body += _opt(2, if_name.encode())          # if_name
    body += _opt(9, b"\x09")                   # if_tsresol = 10^-9
    body += _opt(0, b"")
    return _block(1, body)


def _enhanced_packet(ts_ns: int, data: bytes) -> bytes:
    ts_high = (ts_ns >> 32) & 0xFFFFFFFF
    ts_low = ts_ns & 0xFFFFFFFF
    body = struct.pack("<IIIII", 0, ts_high, ts_low, len(data), len(data))
    body += _pad4(data) + _opt(0, b"")
    return _block(6, body)


def _ip_checksum(header: bytes) -> int:
    total = 0
    for i in range(0, len(header), 2):
        total += (header[i] << 8) + (header[i + 1] if i + 1 < len(header) else 0)
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def ethernet_udp(payload: bytes, *, src_ip: str = "10.0.0.10",
                 dst_ip: str = "239.0.0.1", src_port: int = 5001,
                 dst_port: int = 5001, src_mac: bytes = b"\x02\x00\x00\x00\x00\x10") -> bytes:
    udp_len = 8 + len(payload)
    udp = struct.pack(">HHHH", src_port, dst_port, udp_len, 0) + payload
    src = bytes(int(x) for x in src_ip.split("."))
    dst = bytes(int(x) for x in dst_ip.split("."))
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + udp_len, 0, 0, 64, 17, 0, src, dst)
    ip = ip[:10] + struct.pack(">H", _ip_checksum(ip)) + ip[12:]
    eth = b"\xff" * 6 + src_mac + struct.pack(">H", 0x0800)
    return eth + ip + udp


class PcapngWriter:
    def __init__(self, path: str | Path, if_name: str = "pc5") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("wb")
        self._fh.write(_section_header())
        self._fh.write(_interface(if_name))
        self.count = 0

    def write(self, ts_ns: int, payload: bytes, **kwargs) -> None:
        frame = ethernet_udp(payload, **kwargs)
        self._fh.write(_enhanced_packet(ts_ns, frame))
        self.count += 1

    def close(self) -> None:
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
