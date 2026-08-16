from __future__ import annotations

from pathlib import Path

import pytest

from backend.app import media_preview
from backend.app.media_preview import (
    ensure_media_preview,
    preview_cache_path,
    supports_preview,
)


def test_supported_preview_media_types() -> None:
    assert supports_preview("photo", "image/jpeg")
    assert supports_preview("video", "video/mp4")
    assert supports_preview("animation", "video/mp4")
    assert supports_preview("document", "image/png")
    assert not supports_preview("document", "application/pdf")
    assert not supports_preview("audio", "audio/mpeg")


def test_preview_cache_path_changes_with_media_hash(tmp_path: Path) -> None:
    first = preview_cache_path(tmp_path, 42, "a" * 64)
    second = preview_cache_path(tmp_path, 42, "b" * 64)

    assert first == tmp_path / "aa" / f"42-{'a' * 20}.jpg"
    assert first != second


@pytest.mark.asyncio
async def test_preview_is_generated_once_and_then_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    target = tmp_path / "cache" / "preview.jpg"
    calls: list[float | None] = []

    async def fake_run_ffmpeg(
        _ffmpeg_path: str,
        _source: Path,
        temporary: Path,
        *,
        seek_seconds: float | None,
        max_width: int,
        timeout_seconds: int,
    ) -> tuple[bool, str]:
        calls.append(seek_seconds)
        temporary.write_bytes(b"jpeg")
        return True, ""

    monkeypatch.setattr(media_preview.shutil, "which", lambda _value: "ffmpeg")
    monkeypatch.setattr(media_preview, "_run_ffmpeg", fake_run_ffmpeg)

    assert await ensure_media_preview(source, target, media_type="video") == target
    assert await ensure_media_preview(source, target, media_type="video") == target
    assert target.read_bytes() == b"jpeg"
    assert calls == [1.0]
