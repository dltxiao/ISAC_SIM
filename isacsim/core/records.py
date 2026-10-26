"""观测记录与落盘 Sink。

统一记录格式（schema v1）：
    {"schema","run_id","stream","ts_ns","t_wall_ns","source","entity","payload"}
"""
from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .. import SCHEMA_VERSION


@dataclass(slots=True)
class Record:
    stream: str
    ts_ns: int
    source: str
    entity: str
    payload: dict[str, Any]
    t_wall_ns: int = 0

    def __post_init__(self) -> None:
        if not self.t_wall_ns:
            self.t_wall_ns = time.monotonic_ns()

    def to_dict(self, run_id: str) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "run_id": run_id,
            "stream": self.stream,
            "ts_ns": self.ts_ns,
            "t_wall_ns": self.t_wall_ns,
            "source": self.source,
            "entity": self.entity,
            "payload": self.payload,
        }

    def encode(self, run_id: str) -> bytes:
        return json.dumps(self.to_dict(run_id), ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")


class Sink(Protocol):
    def emit(self, record: Record) -> None: ...

    def close(self) -> None: ...


class NullSink:
    def emit(self, record: Record) -> None:
        return None

    def close(self) -> None:
        return None


class MemorySink:
    """测试与在线检测共用：内存中保留记录。"""

    def __init__(self, limit: int = 200_000) -> None:
        self.records: list[Record] = []
        self.limit = limit

    def emit(self, record: Record) -> None:
        if len(self.records) < self.limit:
            self.records.append(record)

    def close(self) -> None:
        return None

    def clear(self) -> None:
        self.records.clear()


class TeeSink:
    def __init__(self, *sinks: Sink | None) -> None:
        self.sinks = [s for s in sinks if s is not None]

    def emit(self, record: Record) -> None:
        for s in self.sinks:
            s.emit(record)

    def close(self) -> None:
        for s in self.sinks:
            s.close()


class JsonlSink:
    """每个 stream 一个 jsonl（可选 gzip），按需打开文件。"""

    def __init__(self, out_dir: str | Path, run_id: str, compress: bool = False) -> None:
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.compress = compress
        self._files: dict[str, Any] = {}
        self.bytes_written = 0
        self.records_written = 0

    def _open(self, stream: str):
        if stream in self._files:
            return self._files[stream]
        safe = stream.replace("/", "_")
        suffix = ".jsonl.gz" if self.compress else ".jsonl"
        path = self.dir / f"{safe}{suffix}"
        fh = gzip.open(path, "wb", compresslevel=6) if self.compress else path.open("wb")
        self._files[stream] = fh
        return fh

    def emit(self, record: Record) -> None:
        data = record.encode(self.run_id) + b"\n"
        self._open(record.stream).write(data)
        self.bytes_written += len(data)
        self.records_written += 1

    def close(self) -> None:
        for fh in self._files.values():
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass
        self._files.clear()


class ParquetSink:
    """按流缓冲后写 Parquet（列式、压缩），适合批量数据集。"""

    def __init__(self, out_dir: str | Path, run_id: str, flush_rows: int = 50_000) -> None:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - 取决于可选依赖
            raise RuntimeError("Parquet 输出需要 pyarrow：pip install pyarrow") from exc
        self._pa = pa
        self._pq = pq
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.flush_rows = flush_rows
        self._buf: dict[str, list[dict[str, Any]]] = {}
        self._writers: dict[str, Any] = {}
        self.bytes_written = 0
        self.records_written = 0

    def _flush(self, stream: str) -> None:
        rows = self._buf.pop(stream, [])
        if not rows:
            return
        table = self._pa.Table.from_pylist(rows)
        safe = stream.replace("/", "_")
        writer = self._writers.get(stream)
        if writer is None:
            path = self.dir / f"{safe}.parquet"
            writer = self._pq.ParquetWriter(path, table.schema, compression="zstd")
            self._writers[stream] = writer
        writer.write_table(table)
        self.records_written += len(rows)

    def emit(self, record: Record) -> None:
        row = {
            "ts_ns": record.ts_ns,
            "t_wall_ns": record.t_wall_ns,
            "source": record.source,
            "entity": record.entity,
            "payload": json.dumps(record.payload, ensure_ascii=False, separators=(",", ":")),
        }
        self._buf.setdefault(record.stream, []).append(row)
        if len(self._buf[record.stream]) >= self.flush_rows:
            self._flush(record.stream)

    def close(self) -> None:
        for stream in list(self._buf):
            self._flush(stream)
        for writer in self._writers.values():
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
        self._writers.clear()


def make_sink(out_dir: str | Path, run_id: str, streams,
              stats=None) -> Sink:
    """按配置构建 Sink；stats 存在时同时计数。"""
    from .stats import CountingSink

    targets: list[Sink] = []
    if streams.parquet:
        targets.append(ParquetSink(out_dir, run_id))
    if streams.jsonl:
        targets.append(JsonlSink(out_dir, run_id, compress=streams.gzip))
    base: Sink = TeeSink(*targets) if targets else NullSink()
    return CountingSink(base, stats) if stats is not None else base
