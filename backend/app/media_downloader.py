from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from telethon.requestiter import RequestIter


logger = logging.getLogger(__name__)


async def _close_download_stream(stream: Any) -> None:
    if isinstance(stream, RequestIter) and not hasattr(stream, "_sender"):
        return
    close = getattr(stream, "close", None) or getattr(stream, "aclose", None)
    if close is None:
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result


@dataclass(frozen=True, slots=True)
class DownloadPart:
    index: int
    start: int
    end: int
    path: Path

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class ParallelDownloadResult:
    path: str
    sha256: str
    streams: int


def _parts_for(
    temporary: Path,
    file_size: int,
    connection_count: int,
    request_size: int,
) -> list[DownloadPart]:
    total_chunks = math.ceil(file_size / request_size)
    workers = min(connection_count, total_chunks)
    base, extra = divmod(total_chunks, workers)
    parts: list[DownloadPart] = []
    chunk_cursor = 0
    for index in range(workers):
        chunk_count = base + (1 if index < extra else 0)
        start = chunk_cursor * request_size
        chunk_cursor += chunk_count
        end = min(file_size, chunk_cursor * request_size)
        parts.append(
            DownloadPart(
                index=index,
                start=start,
                end=end,
                path=temporary.with_name(f"{temporary.name}.chunk-{index}"),
            )
        )
    return parts


def _manifest_path(temporary: Path) -> Path:
    return temporary.with_name(f"{temporary.name}.parallel.json")


def discard_parallel_download(temporary: Path) -> None:
    """Remove shards before falling back to the ordinary single stream."""
    for stale in temporary.parent.glob(f"{temporary.name}.chunk-*"):
        stale.unlink(missing_ok=True)
    _manifest_path(temporary).unlink(missing_ok=True)
    temporary.with_name(f"{temporary.name}.assembling").unlink(missing_ok=True)


def _prepare_parts(
    temporary: Path,
    file_size: int,
    connection_count: int,
    request_size: int,
) -> list[DownloadPart]:
    parts = _parts_for(temporary, file_size, connection_count, request_size)
    manifest_path = _manifest_path(temporary)
    expected = {
        "version": 1,
        "file_size": file_size,
        "request_size": request_size,
        "parts": [{"index": part.index, "start": part.start, "end": part.end} for part in parts],
    }
    current: dict[str, Any] | None = None
    if manifest_path.exists():
        try:
            current = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
    if current != expected:
        for stale in temporary.parent.glob(f"{temporary.name}.chunk-*"):
            stale.unlink(missing_ok=True)
        manifest_path.write_text(json.dumps(expected, separators=(",", ":")), encoding="utf-8")
    temporary.with_name(f"{temporary.name}.assembling").unlink(missing_ok=True)
    return parts


async def _download_part(
    client: Any,
    source: Any,
    file_size: int,
    part: DownloadPart,
    request_size: int,
    stall_timeout: float,
    on_activity: Callable[[], None] | None,
) -> None:
    received = part.path.stat().st_size if part.path.exists() else 0
    if received > part.size:
        part.path.unlink()
        received = 0
    if received == part.size:
        return

    offset = part.start + received
    remaining = part.end - offset
    request_count = math.ceil(remaining / request_size)
    stream = client.iter_download(
        source,
        offset=offset,
        limit=request_count,
        chunk_size=request_size,
        request_size=request_size,
        file_size=file_size,
    )
    try:
        with part.path.open("ab") as handle:
            while received < part.size:
                try:
                    async with asyncio.timeout(stall_timeout):
                        chunk = await stream.__anext__()
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    raise TimeoutError(
                        f"媒体分片 {part.index + 1} 连续 {stall_timeout:g} 秒没有进度"
                    ) from exc
                if not chunk:
                    raise TimeoutError(f"媒体分片 {part.index + 1} 没有返回新数据")
                data = memoryview(chunk)[: part.size - received]
                handle.write(data)
                received += len(data)
                if on_activity:
                    on_activity()
            handle.flush()
    finally:
        await _close_download_stream(stream)

    if received != part.size:
        raise IOError(
            f"媒体分片 {part.index + 1} 下载不完整（{received}/{part.size} 字节）"
        )


def _assemble_parts(temporary: Path, parts: Sequence[DownloadPart]) -> tuple[str, str]:
    assembling = temporary.with_name(f"{temporary.name}.assembling")
    digest = hashlib.sha256()
    with assembling.open("wb") as target:
        for part in parts:
            with part.path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(chunk)
                    digest.update(chunk)
        target.flush()
    os.replace(assembling, temporary)
    for part in parts:
        part.path.unlink(missing_ok=True)
    _manifest_path(temporary).unlink(missing_ok=True)
    return str(temporary), digest.hexdigest()


async def parallel_download_file(
    client: Any,
    stream_count: int,
    source: Any,
    file_size: int,
    temporary: Path,
    *,
    stall_timeout: float,
    request_size: int = 512 * 1024,
    on_activity: Callable[[], None] | None = None,
) -> ParallelDownloadResult:
    """Download contiguous ranges through concurrent streams on one client.

    Each stream owns an append-only shard, so an interrupted download can
    resume without trusting a sparse file or replaying completed ranges.
    """
    if file_size <= 0:
        raise ValueError("并行下载需要已知文件大小")
    if stream_count < 2:
        raise ValueError("并发下载至少需要两个请求流")
    if request_size <= 0 or request_size % 4096:
        raise ValueError("Telegram 下载分片必须是 4096 字节的整数倍")

    temporary.parent.mkdir(parents=True, exist_ok=True)
    parts = _prepare_parts(temporary, file_size, stream_count, request_size)
    resumed_bytes = sum(
        min(part.path.stat().st_size, part.size) if part.path.exists() else 0
        for part in parts
    )
    started = time.monotonic()
    logger.info(
        "Downloading Telegram media size=%s with %s concurrent streams resumed=%s",
        file_size,
        len(parts),
        resumed_bytes,
    )
    tasks = [asyncio.create_task(_download_part(
            client,
            source,
            file_size,
            part,
            request_size,
            stall_timeout,
            on_activity,
        ), name=f"telegram-media-part-{part.index}") for part in parts]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    path, sha256 = await asyncio.to_thread(_assemble_parts, temporary, parts)
    elapsed = max(time.monotonic() - started, 0.001)
    transferred_bytes = max(file_size - resumed_bytes, 0)
    logger.info(
        "Downloaded Telegram media size=%s concurrent_streams=%s elapsed=%.2fs "
        "speed=%.2f MiB/s",
        file_size,
        len(parts),
        elapsed,
        transferred_bytes / 1024 / 1024 / elapsed,
    )
    return ParallelDownloadResult(path=path, sha256=sha256, streams=len(parts))
