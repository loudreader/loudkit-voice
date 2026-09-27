"""Discord Gateway input and native voice-message replies over the public REST API."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .base import ChannelError, Incoming, encode_voice

API_URL = "https://discord.com/api/v10"
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_API_BYTES = 4 * 1024 * 1024
CDN_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".flac", ".webm"}


def _snowflake(value) -> str:
    result = str(value or "")
    if not result.isascii() or not result.isdigit() or len(result) > 20:
        raise ChannelError("Discord zwrócił nieprawidłowy identyfikator wiadomości lub rozmowy.")
    return result


def _cdn_url(value) -> str:
    if not isinstance(value, str):
        raise ChannelError("Głosówka z Discorda nie ma adresu pobierania.")
    try:
        url = urlsplit(value)
        valid = (
            url.scheme == "https"
            and url.hostname in CDN_HOSTS
            and url.port in (None, 443)
            and not url.username
            and not url.password
            and not url.fragment
            and url.path.startswith(("/attachments/", "/ephemeral-attachments/"))
        )
    except ValueError:
        valid = False
    if not valid:
        raise ChannelError("Discord zwrócił nieobsługiwany adres pobierania głosówki.")
    return value


def _voice_metadata(path: Path) -> tuple[float, str]:
    """Measure the actual audio; sample at most 10 peaks/second and 256 total."""
    try:
        import numpy as np
        import soundfile as sf

        with sf.SoundFile(path) as audio:
            if audio.frames <= 0 or audio.samplerate <= 0:
                raise ValueError("empty audio")
            duration = audio.frames / audio.samplerate
            points = min(256, max(1, math.ceil(duration * 10)))
            peaks = []
            previous = 0
            for point in range(points):
                endpoint = (point + 1) * audio.frames // points
                peak = 0.0
                remaining = endpoint - previous
                while remaining:
                    block = audio.read(min(remaining, 65536), dtype="float32", always_2d=True)
                    if not len(block):
                        raise ValueError("truncated audio")
                    peak = max(peak, float(np.max(np.abs(block))))
                    remaining -= len(block)
                peaks.append(round(min(1.0, max(0.0, peak)) * 255))
                previous = endpoint
            return duration, base64.b64encode(bytes(peaks)).decode("ascii")
    except (ImportError, OSError, RuntimeError, ValueError, OverflowError):
        raise ChannelError("Nie udało się odczytać nagrania odpowiedzi dla Discorda.") from None


class DiscordConnector:
    def __init__(self, config: dict, *, client_factory=None):
        self.config = config
        self.id = str(config["id"])
        self.token = str(config.get("secrets", {}).get("bot_token", "")).strip()
        self.settings = config.get("settings", {})
        self.client_factory = client_factory or httpx.AsyncClient

    def _auth(self) -> dict[str, str]:
        if not self.token or any(char.isspace() for char in self.token):
            raise ChannelError("Wklej prawidłowy token bota Discorda w ustawieniach połączenia.")
        return {"Authorization": f"Bot {self.token}"}

    def _client(self):
        return self.client_factory(timeout=httpx.Timeout(60, connect=15), follow_redirects=False)

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = self._auth()
        try:
            async with self._client() as client:
                async with client.stream(
                    method, API_URL + path, headers=headers, **kwargs
                ) as response:
                    if response.status_code == 429:
                        raise ChannelError(
                            "Discord ograniczył liczbę zapytań bota. Spróbuj ponownie za chwilę."
                        )
                    if response.status_code in (401, 403):
                        raise ChannelError("Discord odrzucił token bota lub uprawnienia do rozmowy.")
                    if not response.is_success:
                        raise ChannelError(f"Discord odrzucił żądanie (HTTP {response.status_code}).")
                    data = bytearray()
                    async for chunk in response.aiter_bytes(65536):
                        if len(data) + len(chunk) > MAX_API_BYTES:
                            raise ChannelError("Discord zwrócił zbyt dużą odpowiedź.")
                        data.extend(chunk)
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("invalid response")
            return result
        except (httpx.HTTPError, ValueError, TypeError):
            raise ChannelError("Nie udało się połączyć z Discordem lub odczytać odpowiedzi. Spróbuj ponownie.") from None

    async def check_connection(self) -> dict:
        user = await self._request("GET", "/users/@me")
        if user.get("bot") is not True:
            raise ChannelError("Wklej token bota Discorda. Token osobistego konta nie jest obsługiwany.")
        return {
            "ok": True,
            "identity": str(user.get("username", "Discord bot")),
            "bot_id": _snowflake(user.get("id")),
        }

    def parse_message(self, message: dict) -> list[Incoming]:
        """Normalize a Gateway event; the core authorizes it before any download."""
        if not isinstance(message, dict):
            return []
        author = message.get("author") or {}
        if not isinstance(author, dict):
            return []
        if author.get("bot") or message.get("webhook_id"):
            return []
        attachments = message.get("attachments") or []
        if not isinstance(attachments, list) or not attachments:
            return []
        try:
            message_id = _snowflake(message.get("id"))
            chat_id = _snowflake(message.get("channel_id"))
            sender_id = _snowflake(author.get("id"))
        except ChannelError:
            return []
        events = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            filename = Path(str(attachment.get("filename") or "voice.ogg")).name
            content_type = str(attachment.get("content_type") or "").lower()
            if not content_type.startswith("audio/") and not (
                not content_type and Path(filename).suffix.lower() in AUDIO_EXTENSIONS
            ):
                continue
            try:
                attachment_id = _snowflake(attachment.get("id"))
                size = int(attachment.get("size", 0))
            except (ChannelError, ValueError, TypeError, OverflowError):
                continue
            if size < 0:
                continue
            events.append(Incoming(
                channel_id=self.id,
                event_id=f"{message_id}:{attachment_id}",
                chat_id=chat_id,
                sender_id=sender_id,
                audio_ref={
                    "url": attachment.get("url"),
                    "size": size,
                    "message_id": message_id,
                },
                filename=filename,
                thread_id=chat_id if message.get("is_thread") else None,
            ))
        return events

    async def run(self, accept) -> None:
        self._auth()
        try:
            import discord
        except ImportError:
            raise ChannelError("Zainstaluj obsługę Discorda: uv sync --extra discord.") from None
        intents = discord.Intents.none()
        intents.dm_messages = True
        if self.settings.get("guild_messages", False):
            intents.guilds = True
            intents.guild_messages = True
            intents.message_content = True
        client = discord.Client(intents=intents)
        ingestion_error = None

        @client.event
        async def on_message(message):
            nonlocal ingestion_error
            payload = {
                "id": message.id,
                "channel_id": message.channel.id,
                "author": {"id": message.author.id, "bot": message.author.bot},
                "webhook_id": message.webhook_id,
                "is_thread": isinstance(message.channel, discord.Thread),
                "attachments": [attachment.to_dict() for attachment in message.attachments],
            }
            try:
                for event in self.parse_message(payload):
                    if not await accept(event):
                        raise ChannelError("Nie udało się zapisać głosówki z Discorda w kolejce.")
            except Exception:
                # discord.py otherwise only logs callback errors and keeps a false healthy state.
                ingestion_error = ChannelError("Nie udało się zapisać głosówki z Discorda w kolejce.")
                await client.close()

        try:
            async with client:
                await client.start(self.token, reconnect=True)
            if ingestion_error:
                raise ingestion_error
        except asyncio.CancelledError:
            raise
        except ChannelError:
            raise
        except Exception:
            raise ChannelError(
                "Discord rozłączył połączenie. Sprawdź token, uprawnienia i intencję Message Content."
            ) from None

    async def download_audio(self, event: Incoming) -> tuple[bytes, str]:
        url = _cdn_url(event.audio_ref.get("url"))
        try:
            size = int(event.audio_ref.get("size", 0))
        except (ValueError, TypeError):
            raise ChannelError("Discord zwrócił nieprawidłowy rozmiar głosówki.") from None
        if size < 0 or size > MAX_AUDIO_BYTES:
            raise ChannelError("Głosówka z Discorda może mieć maksymalnie 25 MiB.")
        try:
            # CDN URLs are signed. Never send the bot token to a CDN or follow its redirects.
            async with self._client() as client:
                async with client.stream("GET", url) as response:
                    if not response.is_success:
                        raise ChannelError("Nie udało się pobrać głosówki z Discorda.")
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > MAX_AUDIO_BYTES:
                        raise ChannelError("Głosówka z Discorda może mieć maksymalnie 25 MiB.")
                    data = bytearray()
                    async for chunk in response.aiter_bytes(65536):
                        if len(data) + len(chunk) > MAX_AUDIO_BYTES:
                            raise ChannelError("Głosówka z Discorda może mieć maksymalnie 25 MiB.")
                        data.extend(chunk)
            if not data:
                raise ChannelError("Głosówka z Discorda jest pusta.")
            return bytes(data), event.filename
        except (httpx.HTTPError, ValueError):
            raise ChannelError("Nie udało się pobrać głosówki z Discorda. Spróbuj ponownie.") from None

    async def send_voice(self, event: Incoming, wav_path: Path, text: str, duration: float) -> dict:
        chat_id = _snowflake(event.thread_id or event.chat_id)
        actual_duration, waveform = await asyncio.to_thread(_voice_metadata, wav_path)
        voice_path = await encode_voice(wav_path, format="opus")
        try:
            if voice_path.stat().st_size > MAX_AUDIO_BYTES - 4096:
                raise ChannelError("Wygenerowana odpowiedź głosowa dla Discorda jest za duża.")
            nonce = hashlib.sha256(f"{self.id}:{event.event_id}".encode()).hexdigest()[:25]
            payload = {
                "flags": 1 << 13,
                "nonce": nonce,
                "enforce_nonce": True,
                "allowed_mentions": {"parse": [], "replied_user": False},
                "attachments": [{
                    "id": 0,
                    "filename": "voice-message.ogg",
                    "duration_secs": actual_duration,
                    "waveform": waveform,
                }],
            }
            # Native voice messages do not allow text content. The transcript stays in LoudTalk.
            with voice_path.open("rb") as voice:
                result = await self._request(
                    "POST", f"/channels/{chat_id}/messages",
                    data={"payload_json": json.dumps(payload)},
                    files={"files[0]": ("voice-message.ogg", voice, "audio/ogg")},
                )
            return {
                "message_id": _snowflake(result.get("id")),
                "chat_id": chat_id,
                "native_voice": True,
            }
        except OSError:
            raise ChannelError("Nie udało się odczytać odpowiedzi głosowej dla Discorda.") from None
        finally:
            if voice_path != wav_path:
                voice_path.unlink(missing_ok=True)


DiscordChannel = DiscordConnector
DiscordAdapter = DiscordConnector
