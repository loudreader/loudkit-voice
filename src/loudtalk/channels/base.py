"""Shared messenger contracts and bounded audio operations."""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

MAX_AUDIO_BYTES = 25 * 1024 * 1024


class ChannelError(RuntimeError):
    """A message safe to display without leaking platform tokens."""


@dataclass
class Incoming:
    channel_id: str
    event_id: str
    chat_id: str
    sender_id: str
    audio_ref: dict
    filename: str
    thread_id: str | None = None

    def to_dict(self):
        return asdict(self)


async def download_limited(client: httpx.AsyncClient, url: str, *, headers=None,
                           max_bytes=MAX_AUDIO_BYTES, allowed_hosts=None) -> bytes:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
        raise ChannelError("Komunikator zwrócił nieprawidłowy adres nagrania.")
    if allowed_hosts is not None and not any(
        host == pattern.lower() or (pattern.startswith(".") and host.endswith(pattern.lower()))
        for pattern in allowed_hosts
    ):
        raise ChannelError("Adres nagrania nie należy do skonfigurowanego komunikatora.")
    try:
        async with client.stream("GET", url, headers=headers, follow_redirects=False) as response:
            response.raise_for_status()
            if int(response.headers.get("content-length", "0")) > max_bytes:
                raise ChannelError("Głosówka może mieć maksymalnie 25 MB.")
            result = bytearray()
            async for chunk in response.aiter_bytes():
                result.extend(chunk)
                if len(result) > max_bytes:
                    raise ChannelError("Głosówka może mieć maksymalnie 25 MB.")
            if not result:
                raise ChannelError("Komunikator zwrócił puste nagranie.")
            return bytes(result)
    except (httpx.HTTPError, ValueError):
        raise ChannelError("Nie udało się pobrać głosówki. Sprawdź uprawnienia połączenia.") from None


async def encode_voice(wav_path: Path, format: str = "opus", speed: float = 1.0) -> Path:
    """Return a disposable encoded file. Caller must unlink it in finally."""
    formats = {
        "opus": ("ogg", ["-c:a", "libopus", "-b:a", "48k", "-application", "voip"]),
        "mp3": ("mp3", ["-c:a", "libmp3lame", "-b:a", "128k"]),
        "m4a": ("m4a", ["-c:a", "aac", "-b:a", "128k"]),
        "aac": ("aac", ["-c:a", "aac", "-b:a", "128k", "-f", "adts"]),
        "flac": ("flac", ["-c:a", "flac"]),
        "pcm": ("pcm", ["-c:a", "pcm_s16le", "-f", "s16le", "-ar", "24000"]),
        "wav": ("wav", ["-c:a", "pcm_s16le"]),
    }
    if format not in formats or not 0.5 <= speed <= 2:
        raise ChannelError("Nieobsługiwany format lub szybkość głosu.")
    executable = shutil.which("ffmpeg")
    if not executable:
        raise ChannelError("Brakuje FFmpeg. Zainstaluj go poleceniem brew install ffmpeg.")
    extension, options = formats[format]
    output = Path(wav_path).parent / f".export-{uuid4().hex}.{extension}"
    argv = [executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(wav_path), "-vn", "-ac", "1"]
    if speed != 1:
        argv += ["-af", f"atempo={speed}"]
    process = await asyncio.create_subprocess_exec(*argv, *options, str(output),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(process.wait(), timeout=90)
        if process.returncode or not output.is_file() or not output.stat().st_size:
            raise ChannelError("Nie udało się przygotować głosówki dla komunikatora.")
        return output
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        output.unlink(missing_ok=True)
        raise
