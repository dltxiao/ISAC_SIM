import struct

from isacsim.io.pcapng import PcapngWriter


def test_pcapng_structure(tmp_path):
    path = tmp_path / "x.pcapng"
    with PcapngWriter(path) as w:
        w.write(1_700_000_000_000_000_000, b"hello")
        w.write(1_700_000_000_100_000_000, b"world")
    data = path.read_bytes()
    assert data[:4] == b"\x0a\x0d\x0d\x0a"          # SHB magic
    # 顺序读取块，校验块长度自洽
    off = 0
    types = []
    while off < len(data):
        btype, blen = struct.unpack_from("<II", data, off)
        assert blen >= 12 and off + blen <= len(data)
        types.append(btype)
        off += blen
    assert types[0] == 0x0A0D0D0A
    assert types[1] == 1                            # IDB
    assert types.count(6) == 2                      # 2 个 EPB
