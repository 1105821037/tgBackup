from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from weakref import WeakValueDictionary


PREVIEW_MEDIA_TYPES = {"photo", "video", "animation"}
preview_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
preview_slots = asyncio.Semaphore(2)
scheduled_preview_targets: set[str] = set()
preview_queue: asyncio.Queue["PreviewJob"] | None = None
preview_worker_tasks: set[asyncio.Task[None]] = set()
logger = logging.getLogger(__name__)


class PreviewGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreviewJob:
    source: Path
    target: Path
    media_type: str
    ffmpeg_path: str
    max_width: int
    timeout_seconds: int


def supports_preview(media_type: str, mime_type: str | None) -> bool:
    return media_type in PREVIEW_MEDIA_TYPES or bool(
        media_type == "document" and mime_type and mime_type.startswith("image/")
    )


def preview_cache_path(
    preview_root: Path,
    media_id: int,
    sha256: str,
) -> Path:
    digest = (sha256 or "unknown").lower()
    return preview_root / digest[:2] / f"{media_id}-{digest[:20]}.jpg"


async def _run_ffmpeg(
    ffmpeg_path: str,
    source: Path,
    target: Path,
    *,
    seek_seconds: float | None,
    max_width: int,
    timeout_seconds: int,
) -> tuple[bool, str]:
    command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if seek_seconds is not None:
        command.extend(("-ss", str(seek_seconds)))
    command.extend(
        (
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale='min({max_width},iw)':-2",
            "-q:v",
            "4",
            str(target),
        )
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=max(1, timeout_seconds)
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        return False, "FFmpeg 生成预览超时"
    message = stderr.decode("utf-8", errors="replace").strip()
    return process.returncode == 0 and target.is_file() and target.stat().st_size > 0, message


async def ensure_media_preview(
    source: Path,
    target: Path,
    *,
    media_type: str,
    ffmpeg_path: str = "ffmpeg",
    max_width: int = 640,
    timeout_seconds: int = 20,
) -> Path:
    if target.is_file() and target.stat().st_size > 0:
        return target
    if not source.is_file():
        raise PreviewGenerationError("媒体文件不存在")
    if not shutil.which(ffmpeg_path):
        raise PreviewGenerationError("服务器未安装 FFmpeg，无法生成媒体预览")

    lock = preview_locks.setdefault(str(target), asyncio.Lock())
    async with lock:
        if target.is_file() and target.stat().st_size > 0:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(
            f".{target.stem}.{os.getpid()}.{uuid.uuid4().hex}.tmp.jpg"
        )
        attempts = (1.0, 0.0) if media_type in {"video", "animation"} else (None,)
        last_error = ""
        try:
            async with preview_slots:
                for seek_seconds in attempts:
                    ok, last_error = await _run_ffmpeg(
                        ffmpeg_path,
                        source,
                        temporary,
                        seek_seconds=seek_seconds,
                        max_width=max(64, min(max_width, 1920)),
                        timeout_seconds=timeout_seconds,
                    )
                    if ok:
                        temporary.replace(target)
                        return target
                    temporary.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)
        detail = last_error[-500:] if last_error else "FFmpeg 未能读取该媒体"
        raise PreviewGenerationError(f"媒体预览生成失败：{detail}")


async def _preview_worker(queue: asyncio.Queue[PreviewJob]) -> None:
    while True:
        job = await queue.get()
        key = str(job.target)
        try:
            await ensure_media_preview(
                job.source,
                job.target,
                media_type=job.media_type,
                ffmpeg_path=job.ffmpeg_path,
                max_width=job.max_width,
                timeout_seconds=job.timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Background media preview failed for %s: %s",
                job.source,
                error,
            )
        finally:
            scheduled_preview_targets.discard(key)
            queue.task_done()


async def start_media_preview_workers(
    worker_count: int = 2,
    queue_size: int = 256,
) -> None:
    global preview_queue
    if any(not task.done() for task in preview_worker_tasks):
        return
    preview_queue = asyncio.Queue(maxsize=max(1, queue_size))
    for index in range(max(1, worker_count)):
        task = asyncio.create_task(
            _preview_worker(preview_queue),
            name=f"media-preview-worker-{index + 1}",
        )
        preview_worker_tasks.add(task)
        task.add_done_callback(preview_worker_tasks.discard)


async def stop_media_preview_workers() -> None:
    global preview_queue
    tasks = tuple(preview_worker_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    preview_worker_tasks.clear()
    queue = preview_queue
    if queue is not None:
        while True:
            try:
                job = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            scheduled_preview_targets.discard(str(job.target))
            queue.task_done()
    preview_queue = None


def schedule_media_preview(
    source: Path,
    target: Path,
    *,
    media_type: str,
    ffmpeg_path: str = "ffmpeg",
    max_width: int = 640,
    timeout_seconds: int = 20,
) -> bool:
    """Queue preview generation without unbounded pending asyncio tasks."""
    key = str(target)
    if target.is_file() or key in scheduled_preview_targets:
        return False
    queue = preview_queue
    if queue is None or queue.full():
        return False
    scheduled_preview_targets.add(key)
    job = PreviewJob(
        source=source,
        target=target,
        media_type=media_type,
        ffmpeg_path=ffmpeg_path,
        max_width=max_width,
        timeout_seconds=timeout_seconds,
    )
    try:
        queue.put_nowait(job)
    except asyncio.QueueFull:
        scheduled_preview_targets.discard(key)
        return False
    return True
