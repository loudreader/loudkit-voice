"""Signed Slack Events API and supported external file upload audio replies."""

import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .base import ChannelError, Incoming, encode_voice

MAX_AUDIO_BYTES = 25 * 1024 * 1024
SLACK_FILE_HOSTS = {"files.slack.com", "files-origin.slack.com"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aac", ".flac", ".webm"}


class SlackAdapter:
    def __init__(self, config: dict):
        self.id = str(config["id"])
        self.secrets = dict(config.get("secrets", {}))
        self.settings = dict(config.get("settings", {}))
        self.bot_user_id = str(self.settings.get("bot_user_id") or "")

    def _secret(self, name: str) -> str:
        value = str(self.secrets.get(name, "")).strip()
        if not value:
            raise ChannelError(f"Slack: add {name} in channel settings.")
        return value

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._secret('bot_token')}"}

    async def _api(self, method: str, **kwargs) -> dict:
        try:
            async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
                response = await client.post(
                    f"https://slack.com/api/{method}", headers=self._headers(), **kwargs,
                )
        except httpx.HTTPError:
            raise ChannelError("Slack: cannot reach the service. Check the connection.") from None
        if response.status_code == 429:
            raise ChannelError("Slack: rate limit reached. Wait before retrying.")
        if not response.is_success:
            raise ChannelError(f"Slack: request failed (HTTP {response.status_code}).")
        try:
            result = response.json()
        except ValueError:
            raise ChannelError("Slack: received an unreadable response.") from None
        if not isinstance(result, dict):
            raise ChannelError("Slack: received an unreadable response.")
        if result.get("ok") is not True:
            error = result.get("error")
            if error in {"invalid_auth", "not_authed", "token_expired", "token_revoked"}:
                hint = "Check or replace the bot token."
            elif error == "missing_scope":
                hint = "Add the required file and message scopes, then reinstall the Slack app."
            elif error in {"not_in_channel", "channel_not_found", "no_permission"}:
                hint = "Invite the bot to this conversation and check file permissions."
            elif error == "file_not_found":
                hint = "The recording is unavailable; send a fresh recording."
            else:
                hint = "Check channel settings and the Slack app's permissions."
            raise ChannelError(f"Slack: the request was rejected. {hint}")
        return result

    async def check_connection(self) -> dict:
        self._secret("signing_secret")
        result = await self._api("auth.test")
        if not result.get("user_id"):
            raise ChannelError("Slack: token check did not return a bot identity.")
        expected_team = str(self.settings.get("team_id") or "")
        if expected_team and result.get("team_id") != expected_team:
            raise ChannelError("Slack: this token belongs to a different workspace.")
        self.bot_user_id = str(result["user_id"])
        return {
            "ok": True, "name": result.get("user") or "Slack bot",
            "team_id": result.get("team_id"), "bot_user_id": self.bot_user_id,
            "detail": "Token verified. Receiving voice also requires signed message events.",
        }

    def verify_webhook(self, raw: bytes, headers: dict, query: dict) -> str | None:
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        timestamp = normalized.get("x-slack-request-timestamp", "")
        try:
            recent = abs(time.time() - int(timestamp)) <= 300
        except ValueError:
            recent = False
        if not recent:
            raise ChannelError("Slack: expired or missing webhook timestamp.")
        signature = "v0=" + hmac.new(
            self._secret("signing_secret").encode(),
            b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256,
        ).hexdigest()
        supplied = normalized.get("x-slack-signature", "")
        if not hmac.compare_digest(signature.encode(), supplied.encode()):
            raise ChannelError("Slack: invalid webhook signature.")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ChannelError("Slack: webhook body must be JSON.") from None
        if not isinstance(payload, dict):
            raise ChannelError("Slack: webhook body must be an object.")
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or not challenge:
                raise ChannelError("Slack: missing URL verification challenge.")
            return challenge
        return None

    @staticmethod
    def _is_audio(file: dict) -> bool:
        return (str(file.get("mimetype", "")).startswith("audio/")
                or file.get("media_display_type") == "audio"
                or file.get("subtype") == "slack_audio"
                or Path(str(file.get("name", ""))).suffix.lower() in AUDIO_SUFFIXES)

    def parse_events(self, payload: dict) -> list[Incoming]:
        if payload.get("type") != "event_callback":
            return []
        expected_team = str(self.settings.get("team_id") or "")
        if expected_team and payload.get("team_id") != expected_team:
            return []
        event = payload.get("event") or {}
        if not isinstance(event, dict) or event.get("type") != "message":
            return []
        if event.get("subtype") not in (None, "file_share") or event.get("bot_id"):
            return []
        sender = str(event.get("user", ""))
        chat_id = str(event.get("channel", ""))
        event_id = str(payload.get("event_id") or event.get("ts") or "")
        if not sender or not chat_id or not event_id or sender == self.bot_user_id:
            return []
        thread_id = str(event.get("thread_ts") or event.get("ts") or "") or None
        incoming = []
        for file in event.get("files", []) or []:
            if not isinstance(file, dict) or not file.get("id") or not self._is_audio(file):
                continue
            if file.get("is_external") or file.get("bot_id") or file.get("bot_user_id"):
                continue
            url = file.get("url_private_download") or file.get("url_private")
            # Slack audio clips may publish an AAC rendition as well as the source file.
            if file.get("media_display_type") == "audio" and file.get("aac"):
                url = file["aac"]
            incoming.append(Incoming(
                channel_id=self.id, event_id=f"{event_id}:{file['id']}", chat_id=chat_id,
                sender_id=sender, thread_id=thread_id,
                audio_ref={"file_id": str(file["id"]), "url": str(url or ""),
                           "size": file.get("size", 0)},
                filename=Path(str(file.get("name") or "voice.m4a")).name,
            ))
        return incoming

    @staticmethod
    def _file_url(value: str) -> str:
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme == "https" and parsed.port in (None, 443)
                     and parsed.hostname in SLACK_FILE_HOSTS
                     and not parsed.username and not parsed.password)
        except ValueError:
            valid = False
        if not valid:
            raise ChannelError("Slack: refused an unexpected file host.")
        return value

    async def download_audio(self, event: Incoming) -> tuple[bytes, str]:
        url = str(event.audio_ref.get("url") or "")
        size = event.audio_ref.get("size") or 0
        if not url:
            file_id = str(event.audio_ref.get("file_id") or "")
            if not re.fullmatch(r"F[A-Z0-9]+", file_id):
                raise ChannelError("Slack: invalid audio file ID.")
            result = await self._api("files.info", data={"file": file_id})
            file = result.get("file") or {}
            if not isinstance(file, dict) or not self._is_audio(file) or file.get("is_external"):
                raise ChannelError("Slack: attachment is not a hosted audio recording.")
            url = str(file.get("url_private_download") or file.get("url_private") or "")
            size = file.get("size") or 0
        if int(size) > MAX_AUDIO_BYTES:
            raise ChannelError("Slack: recording exceeds 25 MB. Send a shorter recording.")
        url = self._file_url(url)
        audio = bytearray()
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                async with client.stream("GET", url, headers=self._headers()) as response:
                    if not response.is_success:
                        raise ChannelError("Slack: audio download failed. Check files:read access.")
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        audio.extend(chunk)
                        if len(audio) > MAX_AUDIO_BYTES:
                            raise ChannelError("Slack: recording exceeds 25 MB.")
        except httpx.HTTPError:
            raise ChannelError("Slack: interrupted audio download. Check the connection.") from None
        if not audio:
            raise ChannelError("Slack: received an empty audio recording.")
        return bytes(audio), event.filename

    async def send_voice(self, event: Incoming, wav_path: Path, text: str,
                         duration: float) -> dict:
        path = await encode_voice(wav_path, format="mp3")
        try:
            size = path.stat().st_size
            if size > MAX_AUDIO_BYTES:
                raise ChannelError("Slack: reply exceeds 25 MB. Choose a shorter agent response.")
            ticket = await self._api("files.getUploadURLExternal", data={
                "filename": "reply.mp3", "length": str(size),
            })
            url = self._file_url(str(ticket.get("upload_url") or ""))
            file_id = str(ticket.get("file_id") or "")
            if not re.fullmatch(r"F[A-Z0-9]+", file_id):
                raise ChannelError("Slack: upload ticket did not contain a file ID.")
            try:
                async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                    # Ticket URL is already authorized. Do not expose the bot token to uploads.
                    response = await client.post(
                        url, content=path.read_bytes(), headers={"Content-Type": "audio/mpeg"},
                    )
            except httpx.HTTPError:
                raise ChannelError("Slack: audio upload interrupted. Check the connection.") from None
            if not response.is_success:
                raise ChannelError("Slack: audio upload was rejected. Try again.")
            body = {"files": [{"id": file_id, "title": "Voice reply"}], "channel_id": event.chat_id}
            if event.thread_id:
                body["thread_ts"] = event.thread_id
            if text:
                body["initial_comment"] = text[:3000]
            result = await self._api("files.completeUploadExternal", json=body)
            if not any(isinstance(file, dict) and file.get("id") == file_id
                       for file in result.get("files", [])):
                raise ChannelError("Slack: file upload completion was not confirmed.")
            return {"file_id": file_id, "native_voice": False, "kind": "audio_attachment"}
        finally:
            path.unlink(missing_ok=True)
