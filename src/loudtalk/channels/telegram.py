"""Telegram Bot API voice transport; one poller owns one dedicated bot token."""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from .base import ChannelError, Incoming, encode_voice

API_ROOT = "https://api.telegram.org"
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Telegram's hosted getFile limit.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_API_BYTES = 4 * 1024 * 1024


def _telegram_error(code: int) -> ChannelError:
    if code in (401, 404):
        return ChannelError("Telegram: token bota jest nieprawidłowy. Sprawdź go w BotFather.")
    if code == 403:
        return ChannelError("Telegram: bot nie ma dostępu do tej rozmowy lub został zablokowany.")
    if code == 409:
        return ChannelError(
            "Telegram: ten bot ma już inny odbiornik wiadomości. "
            "Podłącz głos w istniejącym agencie albo użyj osobnego bota."
        )
    if code == 429:
        return ChannelError("Telegram: limit zapytań. Odczekaj chwilę i ponów połączenie.")
    return ChannelError("Telegram nie przyjął żądania. Sprawdź połączenie i uprawnienia bota.")


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


class TelegramChannel:
    def __init__(self, config: dict, *, client_factory: Callable = httpx.AsyncClient):
        self.config = config
        self.channel_id = str(config["id"])
        self.settings = config.get("settings", {})
        self._token = str(config.get("secrets", {}).get("bot_token", "")).strip()
        self._client_factory = client_factory
        self._offset: int | None = None

    def _url(self, method: str) -> str:
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", self._token):
            raise ChannelError("Telegram: wklej token bota otrzymany od BotFather.")
        return f"{API_ROOT}/bot{self._token}/{method}"

    async def _api(self, method: str, *, body: dict | None = None, files: dict | None = None):
        url = self._url(method)
        try:
            async with self._client_factory(
                timeout=httpx.Timeout(65.0, connect=10.0), follow_redirects=False
            ) as client:
                args = {"data": body or {}, "files": files} if files else {"json": body or {}}
                async with client.stream("POST", url, **args) as response:
                    if response.status_code >= 300:
                        raise _telegram_error(response.status_code)
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_API_BYTES:
                            raise ChannelError("Telegram zwrócił zbyt dużą odpowiedź.")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError
            if result.get("ok") is not True:
                raise _telegram_error(_int(result.get("error_code")) or 400)
            return result.get("result")
        except (httpx.HTTPError, ValueError, TypeError):
            # httpx exceptions can include the bot token embedded in the URL.
            raise ChannelError("Nie udało się połączyć z Telegramem. Spróbuj ponownie.") from None

    async def check_connection(self) -> dict:
        me = await self._api("getMe")
        webhook = await self._api("getWebhookInfo")
        if not isinstance(me, dict) or not me.get("is_bot") or not me.get("id"):
            raise ChannelError("Telegram nie potwierdził konta bota.")
        if not isinstance(webhook, dict):
            raise ChannelError("Nie udało się sprawdzić sposobu odbioru wiadomości Telegrama.")
        if webhook.get("url"):
            raise ChannelError(
                "Ten bot Telegrama jest już podłączony przez webhook. "
                "Włącz LoudTalk jako dostawcę głosu w istniejącym agencie "
                "albo podłącz osobnego bota."
            )
        return {"ok": True, "identity": f"@{me.get('username', me['id'])}", "bot_id": str(me["id"])}

    def parse_update(self, update: dict) -> Incoming | None:
        """Normalize a new user audio message without downloading any attachment."""
        if not isinstance(update, dict):
            return None
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        sender, chat = message.get("from"), message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict) or sender.get("is_bot"):
            return None
        update_id = _int(update.get("update_id"))
        if (
            not sender.get("id")
            or not chat.get("id")
            or not message.get("message_id")
            or update_id is None
        ):
            return None
        voice = message.get("voice")
        audio = voice or message.get("audio")
        document = message.get("document")
        if not audio and isinstance(document, dict):
            mime = document.get("mime_type", "")
            if isinstance(mime, str) and mime.startswith("audio/"):
                audio = document
        if not isinstance(audio, dict) or not isinstance(audio.get("file_id"), str):
            return None
        filename = "voice.ogg" if voice else str(audio.get("file_name") or "audio.ogg")
        # A remote file name is display metadata, never a local path.
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:180] or "voice.ogg"
        thread = message.get("message_thread_id")
        return Incoming(
            channel_id=self.channel_id,
            event_id=str(update_id),
            chat_id=str(chat["id"]),
            sender_id=str(sender["id"]),
            audio_ref={
                "file_id": audio["file_id"],
                "file_size": audio.get("file_size"),
                "mime_type": audio.get("mime_type", "audio/ogg"),
                "message_id": str(message["message_id"]),
                "update_id": update.get("update_id"),
            },
            filename=filename,
            thread_id=str(thread) if thread is not None else None,
        )

    async def run(self, accept: Callable[[Incoming], Awaitable[Any]]) -> None:
        await self.check_connection()
        while True:
            body: dict = {"timeout": 50, "limit": 100, "allowed_updates": ["message"]}
            if self._offset is not None:
                body["offset"] = self._offset
            # Auth, competing pollers and rate limits propagate to the supervisor.
            updates = await self._api("getUpdates", body=body)
            if not isinstance(updates, list):
                raise ChannelError("Telegram zwrócił nieprawidłową listę wiadomości.")
            for update in updates:
                update_id = _int(update.get("update_id")) if isinstance(update, dict) else None
                if update_id is None:
                    raise ChannelError("Telegram zwrócił wiadomość bez identyfikatora.")
                event = self.parse_update(update)
                if event is not None:
                    # Telegram receives an acknowledgement only in the NEXT request.
                    # Never move the offset across an event not yet durably enqueued.
                    if await accept(event) is False:
                        raise ChannelError(
                            "Nie zapisano głosówki w kolejce. Telegram spróbuje dostarczyć ją ponownie."
                        )
                self._offset = update_id + 1
            # getUpdates normally waits; allow cancellation even with a synthetic
            # empty immediate response (e.g. tests or a transient proxy response).
            await asyncio.sleep(0)

    async def download_audio(self, event: Incoming) -> tuple[bytes, str]:
        size = _int(event.audio_ref.get("file_size"))
        if size is not None and size > MAX_DOWNLOAD_BYTES:
            raise ChannelError("Telegram: głosówka przekracza limit pobierania 20 MB.")
        file_id = event.audio_ref.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            raise ChannelError("Telegram: głosówka nie zawiera identyfikatora pliku.")
        result = await self._api("getFile", body={"file_id": file_id})
        if not isinstance(result, dict):
            raise ChannelError("Telegram nie udostępnił pliku głosówki.")
        size = _int(result.get("file_size"))
        if size is not None and size > MAX_DOWNLOAD_BYTES:
            raise ChannelError("Telegram: głosówka przekracza limit pobierania 20 MB.")
        path = result.get("file_path")
        # Reject schemes, query strings, percent encodings and path traversal;
        # only the fixed Telegram host can ever receive a bot token.
        if (
            not isinstance(path, str)
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", path)
            or path.startswith("/")
            or any(part in ("", ".", "..") for part in path.split("/"))
        ):
            raise ChannelError("Telegram zwrócił nieprawidłowy adres pliku głosówki.")
        self._url("getFile")  # Validate token before constructing the file URL.
        url = f"{API_ROOT}/file/bot{self._token}/{path}"
        try:
            async with self._client_factory(timeout=60.0, follow_redirects=False) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code >= 300:
                        raise ChannelError("Nie udało się pobrać głosówki z Telegrama.")
                    declared = _int(response.headers.get("content-length"))
                    if declared is not None and declared > MAX_DOWNLOAD_BYTES:
                        raise ChannelError("Telegram: głosówka przekracza limit pobierania 20 MB.")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_DOWNLOAD_BYTES:
                            raise ChannelError("Telegram: głosówka przekracza limit pobierania 20 MB.")
            if not data:
                raise ChannelError("Telegram udostępnił pusty plik głosówki.")
            return bytes(data), event.filename
        except httpx.HTTPError:
            raise ChannelError("Nie udało się pobrać głosówki z Telegrama.") from None

    async def send_voice(
        self, event: Incoming, wav_path: Path, text: str, duration: float
    ) -> dict:
        if not math.isfinite(duration) or duration < 0:
            raise ChannelError("Nie udało się odczytać czasu trwania odpowiedzi głosowej.")
        opus = await encode_voice(wav_path, format="opus")
        try:
            if opus.stat().st_size > MAX_UPLOAD_BYTES:
                raise ChannelError("Odpowiedź głosowa przekracza limit 25 MB.")
            body = {"chat_id": event.chat_id, "duration": str(math.ceil(duration))}
            if event.thread_id is not None:
                body["message_thread_id"] = event.thread_id
            message_id = _int(event.audio_ref.get("message_id"))
            if message_id is not None:
                body["reply_parameters"] = json.dumps(
                    {"message_id": message_id, "allow_sending_without_reply": True}
                )
            with opus.open("rb") as audio:
                result = await self._api(
                    "sendVoice", body=body, files={"voice": ("reply.ogg", audio, "audio/ogg")}
                )
            if not isinstance(result, dict) or not result.get("message_id"):
                raise ChannelError("Telegram nie potwierdził wysłania głosówki.")
            return {"message_id": str(result["message_id"]), "native_voice": True}
        finally:
            opus.unlink(missing_ok=True)


TelegramAdapter = TelegramChannel
TelegramConnector = TelegramChannel
