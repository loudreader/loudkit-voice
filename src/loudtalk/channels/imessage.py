"""iMessage audio through a user-configured BlueBubbles server.

BlueBubbles webhooks have no built-in signature. The registered URL must include
LoudTalk's independent, random webhook token; the server API password is never
accepted as a substitute. This module never reads Messages' database or launches
AppleScript locally.
"""

from __future__ import annotations

import hmac
import ipaddress
import re
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

from .base import ChannelError, Incoming, encode_voice

MAX_AUDIO_BYTES = 25 * 1024 * 1024
_GUID = re.compile(r"[A-Za-z0-9_:.\-]{1,300}\Z")
_AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav", ".caf", ".aac", ".ogg", ".opus", ".aiff"}
_AUDIO_UTIS = {"public.audio", "com.apple.coreaudio-format", "public.mpeg-4-audio"}
_LAN_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


def _server_url(value: str) -> str:
    """Allow explicit HTTPS servers and plain HTTP only on localhost/private IPs."""
    if not isinstance(value, str) or not value.strip():
        raise ChannelError("Podaj adres serwera BlueBubbles.")
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ChannelError("Nieprawidłowy adres serwera BlueBubbles.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or ".." in parsed.path
        or not re.fullmatch(r"[A-Za-z0-9/_\-]*", parsed.path)
        or any(ord(char) < 33 for char in value)
    ):
        raise ChannelError("Użyj adresu HTTP(S) BlueBubbles bez hasła, parametrów i fragmentu.")
    if parsed.scheme == "http" and parsed.hostname.lower() != "localhost":
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ChannelError("Dla HTTP podaj localhost lub prywatny adres IP serwera.") from exc
        if not address.is_loopback and not any(address in network for network in _LAN_NETWORKS):
            raise ChannelError("Poza siecią lokalną serwer BlueBubbles musi używać HTTPS.")
    return value


def _guid(value: object) -> str | None:
    if isinstance(value, str) and _GUID.fullmatch(value) and ".." not in value:
        return value
    return None


def _identity(value: object) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= 512 and all(ord(c) >= 32 for c in value):
        return value
    return None


def _filename(value: object, *, native: bool = False) -> str:
    # Incoming paths and Content-Disposition values never become local paths.
    if isinstance(value, str):
        name = value.replace("\\", "/").rsplit("/", 1)[-1]
        if 0 < len(name) <= 200 and all(ord(c) >= 32 for c in name):
            return name
    return "voice.caf" if native else "voice.m4a"


class IMessageAdapter:
    def __init__(self, config: dict, *, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.channel_id = config["id"]
        self.settings = config.get("settings") or {}
        self.secrets = config.get("secrets") or {}
        self.server_url = _server_url(self.settings.get("server_url", ""))
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(90, connect=10),
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        )

    def _auth(self) -> dict:
        password = self.secrets.get("password")
        if not isinstance(password, str) or not password:
            raise ChannelError("Podaj hasło API serwera BlueBubbles.")
        return {"password": password}

    async def _json(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with self._client() as client:
                response = await client.request(
                    method, self.server_url + path, params=self._auth(), **kwargs
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("status"), int):
                raise ChannelError("BlueBubbles zwrócił nieprawidłową odpowiedź.")
            if not 200 <= body["status"] < 300:
                raise ChannelError("BlueBubbles odrzucił żądanie. Sprawdź połączenie i hasło API.")
            data = body.get("data")
            if not isinstance(data, dict):
                raise ChannelError("BlueBubbles nie zwrócił danych odpowiedzi.")
            return data
        except (httpx.HTTPError, ValueError):
            # httpx error strings contain the URL, including the API password.
            raise ChannelError("Nie udało się połączyć z BlueBubbles. Sprawdź adres i hasło API.") from None

    async def check_connection(self) -> dict:
        data = await self._json("GET", "/api/v1/server")
        native_available = data.get("private_api") is True and data.get("helper_connected") is True
        return {
            "ok": True,
            "identity": "BlueBubbles",
            "server_version": data.get("server_version"),
            "native_audio_available": native_available,
            "reply_format": "native_audio" if self.settings.get("native_audio_message") else "audio_attachment",
            "detail": "Połączono z BlueBubbles. Odbiór wymaga webhooka i zatwierdzenia rozmowy.",
        }

    def verify_webhook(self, raw: bytes, headers: dict, query: dict) -> str | None:
        expected = self.secrets.get("webhook_secret")
        supplied = query.get("token")
        if (
            not isinstance(expected, str)
            or len(expected) < 32
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied.encode(), expected.encode())
        ):
            raise ChannelError("Nieprawidłowy token webhooka iMessage.")
        return None

    def parse_events(self, payload: dict) -> list[Incoming]:
        if not isinstance(payload, dict) or payload.get("type") != "new-message":
            return []
        message = payload.get("data")
        if not isinstance(message, dict) or message.get("isFromMe") is not False:
            return []
        message_guid = _guid(message.get("guid"))
        handle = message.get("handle")
        sender_id = _identity(handle.get("address")) if isinstance(handle, dict) else None
        chats = message.get("chats")
        if not message_guid or not sender_id or not isinstance(chats, list):
            return []
        chat_guids = {
            valid for chat in chats if isinstance(chat, dict)
            if (valid := _identity(chat.get("guid")))
        }
        # Never guess a destination when a bridge event contains multiple chats.
        if len(chat_guids) != 1:
            return []
        chat_id = next(iter(chat_guids))
        if not chat_id.startswith("iMessage;"):
            return []
        attachments = message.get("attachments")
        if not isinstance(attachments, list):
            return []
        events = []
        seen = set()
        for attachment in attachments:
            if not isinstance(attachment, dict) or attachment.get("isOutgoing") is True:
                continue
            attachment_guid = _guid(attachment.get("guid"))
            if not attachment_guid or attachment_guid in seen:
                continue
            mime = attachment.get("mimeType")
            mime = mime.lower().split(";", 1)[0] if isinstance(mime, str) else ""
            native = message.get("isAudioMessage") is True
            filename = _filename(attachment.get("transferName"), native=native)
            uti = attachment.get("uti")
            known_uti = isinstance(uti, str) and uti in _AUDIO_UTIS
            named_audio = isinstance(attachment.get("transferName"), str) and (
                Path(filename).suffix.lower() in _AUDIO_SUFFIXES
            )
            audio = mime.startswith("audio/") or (
                mime in {"", "application/octet-stream"}
                and (native or named_audio or known_uti)
            )
            if not audio:
                continue
            size = attachment.get("totalBytes")
            if isinstance(size, int) and (size < 0 or size > MAX_AUDIO_BYTES):
                continue
            seen.add(attachment_guid)
            events.append(Incoming(
                channel_id=self.channel_id,
                event_id=f"{message_guid}:{attachment_guid}",
                chat_id=chat_id,
                sender_id=sender_id,
                audio_ref={"guid": attachment_guid, "size": size, "mime_type": mime},
                filename=filename,
            ))
        return events

    async def download_audio(self, event: Incoming) -> tuple[bytes, str]:
        attachment_guid = _guid(event.audio_ref.get("guid"))
        if not attachment_guid or event.channel_id != self.channel_id:
            raise ChannelError("Nieprawidłowy identyfikator nagrania iMessage.")
        url = self.server_url + "/api/v1/attachment/" + quote(attachment_guid, safe="") + "/download"
        params = {**self._auth(), "original": "true", "force": "false"}
        try:
            async with self._client() as client:
                async with client.stream("GET", url, params=params) as response:
                    response.raise_for_status()
                    length = response.headers.get("content-length", "")
                    if length.isdigit() and int(length) > MAX_AUDIO_BYTES:
                        raise ChannelError("Nagranie iMessage przekracza limit 25 MB.")
                    content = bytearray()
                    async for part in response.aiter_bytes(chunk_size=64 * 1024):
                        content.extend(part)
                        if len(content) > MAX_AUDIO_BYTES:
                            raise ChannelError("Nagranie iMessage przekracza limit 25 MB.")
            if not content:
                raise ChannelError("Nagranie iMessage jest puste lub jeszcze niedostępne.")
            return bytes(content), _filename(event.filename)
        except httpx.HTTPError:
            raise ChannelError("Nie udało się pobrać nagrania z BlueBubbles.") from None

    async def send_voice(self, event: Incoming, wav_path: Path, text: str, duration: float) -> dict:
        if event.channel_id != self.channel_id or not _identity(event.chat_id):
            raise ChannelError("Nieprawidłowa rozmowa iMessage.")
        if not event.chat_id.startswith("iMessage;"):
            raise ChannelError("Odpowiedzi głosowe wymagają rozmowy iMessage.")
        native = self.settings.get("native_audio_message") is True
        if native:
            status = await self.check_connection()
            if not status["native_audio_available"]:
                raise ChannelError("Natywne głosówki wymagają aktywnego Private API BlueBubbles.")
        audio_path = await encode_voice(wav_path, format="mp3" if native else "m4a")
        # iMessage attachments have no caption field. Do not send a second message
        # implicitly, and never let agent output provide a recipient or API URL.
        name = "LoudTalk.mp3" if native else "LoudTalk.m4a"
        fields = {
            "chatGuid": event.chat_id,
            "tempGuid": str(uuid4()),
            "name": name,
            "method": "private-api" if native else "apple-script",
            "isAudioMessage": "true" if native else "false",
        }
        try:
            if audio_path.stat().st_size > MAX_AUDIO_BYTES:
                raise ChannelError("Odpowiedź głosowa przekracza limit 25 MB.")
            with audio_path.open("rb") as audio_file:
                data = await self._json(
                    "POST", "/api/v1/message/attachment", data=fields,
                    files={"attachment": (name, audio_file, "audio/mpeg" if native else "audio/mp4")},
                )
        finally:
            audio_path.unlink(missing_ok=True)
        message_id = _identity(data.get("guid"))
        if not message_id or data.get("error"):
            raise ChannelError("BlueBubbles nie potwierdził wysłania głosówki.")
        return {
            "message_id": message_id,
            "chat_id": event.chat_id,
            "kind": "native_audio" if native else "audio_attachment",
        }
