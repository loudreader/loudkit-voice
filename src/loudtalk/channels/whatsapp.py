"""WhatsApp Cloud API voice messages, authenticated with Meta app signatures."""

import hashlib
import hmac
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .base import ChannelError, Incoming, encode_voice

MAX_AUDIO_BYTES = 16 * 1024 * 1024  # WhatsApp's audio limit is below the app's 25 MB limit.
DEFAULT_GRAPH_VERSION = "v24.0"


class WhatsAppAdapter:
    def __init__(self, config: dict):
        self.id = str(config["id"])
        self.secrets = dict(config.get("secrets", {}))
        self.settings = dict(config.get("settings", {}))

    def _secret(self, name: str) -> str:
        value = str(self.secrets.get(name, "")).strip()
        if not value:
            raise ChannelError(f"WhatsApp: add {name} in channel settings.")
        return value

    def _phone(self) -> str:
        value = str(self.settings.get("phone_number_id") or
                    self.secrets.get("phone_number_id") or "")
        if not re.fullmatch(r"[0-9]+", value):
            raise ChannelError("WhatsApp: enter Meta's numeric Phone Number ID.")
        return value

    def _url(self, resource: str) -> str:
        version = str(self.settings.get("graph_version") or DEFAULT_GRAPH_VERSION)
        if not re.fullmatch(r"v[0-9]+\.0", version):
            raise ChannelError("WhatsApp: Graph API version must look like v24.0.")
        return f"https://graph.facebook.com/{version}/{resource}"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._secret('access_token')}"}

    async def _api(self, method: str, resource: str, **kwargs) -> dict:
        try:
            async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
                response = await client.request(
                    method, self._url(resource), headers=self._headers(), **kwargs,
                )
        except httpx.HTTPError:
            raise ChannelError("WhatsApp: cannot reach Meta. Check the connection and retry.") from None
        if response.status_code in (401, 403):
            raise ChannelError("WhatsApp: check the access token and WhatsApp permissions.")
        if response.status_code == 429:
            raise ChannelError("WhatsApp: Meta rate limit reached. Wait before retrying.")
        if not response.is_success:
            raise ChannelError(
                f"WhatsApp: Meta rejected the request (HTTP {response.status_code}). "
                "Check the business number, token permissions and conversation window."
            )
        try:
            result = response.json()
        except ValueError:
            raise ChannelError("WhatsApp: Meta returned an unreadable response.") from None
        if not isinstance(result, dict) or result.get("error"):
            raise ChannelError("WhatsApp: Meta rejected the request. Check channel settings.")
        return result

    async def check_connection(self) -> dict:
        self._secret("app_secret")
        self._secret("verify_token")
        result = await self._api(
            "GET", self._phone(), params={"fields": "id,display_phone_number,verified_name"},
        )
        if str(result.get("id", "")) != self._phone():
            raise ChannelError("WhatsApp: Meta did not confirm the configured phone number.")
        return {
            "ok": True,
            "phone_number_id": result["id"],
            "name": result.get("verified_name") or result.get("display_phone_number") or "WhatsApp",
            "detail": "Token verified. Receiving voice also requires the messages webhook.",
        }

    def verify_webhook(self, raw: bytes, headers: dict, query: dict) -> str | None:
        if "hub.mode" in query:
            token = str(query.get("hub.verify_token", ""))
            if query.get("hub.mode") != "subscribe" or not hmac.compare_digest(
                token.encode(), self._secret("verify_token").encode(),
            ):
                raise ChannelError("WhatsApp: webhook verification token does not match.")
            challenge = query.get("hub.challenge")
            if not isinstance(challenge, str) or not challenge:
                raise ChannelError("WhatsApp: missing webhook verification challenge.")
            return challenge
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        expected = "sha256=" + hmac.new(
            self._secret("app_secret").encode(), raw, hashlib.sha256,
        ).hexdigest()
        supplied = normalized.get("x-hub-signature-256", "")
        if not hmac.compare_digest(expected.encode(), supplied.encode()):
            raise ChannelError("WhatsApp: invalid webhook signature.")
        return None

    def parse_events(self, payload: dict) -> list[Incoming]:
        if payload.get("object") != "whatsapp_business_account":
            return []
        incoming = []
        phone = self._phone()
        for entry in payload.get("entry", []) or []:
            if not isinstance(entry, dict):
                continue
            for change in entry.get("changes", []) or []:
                if not isinstance(change, dict) or change.get("field") != "messages":
                    continue
                value = change.get("value") or {}
                if not isinstance(value, dict):
                    continue
                metadata = value.get("metadata") or {}
                if not isinstance(metadata, dict) or str(metadata.get("phone_number_id")) != phone:
                    continue
                for message in value.get("messages", []) or []:
                    if not isinstance(message, dict) or message.get("type") != "audio":
                        continue
                    audio = message.get("audio") or {}
                    sender = str(message.get("from", ""))
                    event_id = str(message.get("id", ""))
                    if not isinstance(audio, dict) or not audio.get("id") or not event_id:
                        continue
                    if not sender.isdigit() or message.get("from_me") or message.get("is_echo"):
                        continue
                    if sender == re.sub(r"[^0-9]", "", str(metadata.get("display_phone_number", ""))):
                        continue
                    incoming.append(Incoming(
                        channel_id=self.id, event_id=event_id, chat_id=sender, sender_id=sender,
                        audio_ref={"media_id": str(audio["id"]),
                                   "mime_type": str(audio.get("mime_type", "audio/ogg"))},
                        filename="voice.ogg" if "ogg" in str(audio.get("mime_type", "ogg"))
                        else "voice.m4a",
                    ))
        return incoming

    @staticmethod
    def _media_url(value: str) -> str:
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme == "https" and parsed.port in (None, 443)
                     and parsed.hostname in {"lookaside.fbsbx.com", "lookaside.facebook.com"}
                     and not parsed.username and not parsed.password)
        except ValueError:
            valid = False
        if not valid:
            raise ChannelError("WhatsApp: refused an unexpected media download host.")
        return value

    async def download_audio(self, event: Incoming) -> tuple[bytes, str]:
        media_id = str(event.audio_ref.get("media_id", ""))
        if not re.fullmatch(r"[0-9]+", media_id):
            raise ChannelError("WhatsApp: invalid voice attachment ID.")
        metadata = await self._api(
            "GET", media_id, params={"phone_number_id": self._phone()},
        )
        mime = str(metadata.get("mime_type", ""))
        if not mime.startswith("audio/"):
            raise ChannelError("WhatsApp: attachment is not an audio recording.")
        if int(metadata.get("file_size") or 0) > MAX_AUDIO_BYTES:
            raise ChannelError("WhatsApp: voice recording exceeds 16 MB. Send a shorter recording.")
        url = self._media_url(str(metadata.get("url", "")))
        audio = bytearray()
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                async with client.stream("GET", url, headers=self._headers()) as response:
                    if not response.is_success:
                        raise ChannelError("WhatsApp: audio download failed. Try a fresh recording.")
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        audio.extend(chunk)
                        if len(audio) > MAX_AUDIO_BYTES:
                            raise ChannelError("WhatsApp: voice recording exceeds 16 MB.")
        except httpx.HTTPError:
            raise ChannelError("WhatsApp: interrupted audio download. Check the connection.") from None
        if not audio:
            raise ChannelError("WhatsApp: received an empty audio recording.")
        suffix = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/aac": ".aac",
                  "audio/amr": ".amr", "audio/mp4": ".m4a"}.get(mime.split(";")[0], ".audio")
        return bytes(audio), f"voice{suffix}"

    async def send_voice(self, event: Incoming, wav_path: Path, text: str,
                         duration: float) -> dict:
        path = await encode_voice(wav_path, format="opus")
        try:
            if path.stat().st_size > MAX_AUDIO_BYTES:
                raise ChannelError("WhatsApp: reply exceeds 16 MB. Choose a shorter agent response.")
            with path.open("rb") as recording:
                media = await self._api(
                    "POST", f"{self._phone()}/media", data={"messaging_product": "whatsapp"},
                    files={"file": ("reply.ogg", recording, "audio/ogg; codecs=opus")},
                )
            media_id = str(media.get("id", ""))
            if not media_id:
                raise ChannelError("WhatsApp: Meta did not confirm the audio upload.")
            result = await self._api("POST", f"{self._phone()}/messages", json={
                "messaging_product": "whatsapp", "recipient_type": "individual", "to": event.chat_id,
                "context": {"message_id": event.event_id}, "type": "audio",
                "audio": {"id": media_id, "voice": True},
            })
            messages = result.get("messages") or []
            if not messages or not isinstance(messages[0], dict) or not messages[0].get("id"):
                raise ChannelError("WhatsApp: Meta did not confirm the voice message.")
            return {"message_id": messages[0]["id"], "media_id": media_id, "native_voice": True}
        finally:
            path.unlink(missing_ok=True)
