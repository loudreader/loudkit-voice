"""Opt-in real speech + Codex + Telegram-protocol smoke, without a Telegram account.

The running local LoudTalk server supplies real STT/TTS. The production Telegram
adapter uses an explicit in-memory HTTP transport: no Telegram traffic leaves
this process. Codex uses its existing login and standard permissions in a fresh
temporary Git repository. This consumes one ordinary Codex turn.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx

from loudtalk.channels.base import encode_voice
from loudtalk.channels.manager import ChannelManager
from loudtalk.channels.telegram import TelegramAdapter
from loudtalk.store import Store


class LocalSpeech:
    """Use the real running engines, avoiding another model instance."""

    def __init__(self, base_url, directory):
        self.base_url = base_url
        self.directory = directory

    def synthesize(self, text, voice):
        response = httpx.post(
            self.base_url + "/api/voice-preview",
            json={"text": text, "voice": voice},
            timeout=300,
        )
        response.raise_for_status()
        return response.json()

    def transcribe(self, path):
        with path.open("rb") as source:
            response = httpx.post(
                self.base_url + "/api/transcribe",
                files={"file": (path.name, source, "application/octet-stream")},
                timeout=300,
            )
        response.raise_for_status()
        return response.json()

    def resolve_audio(self, audio_id):
        if len(audio_id) != 32 or any(c not in "0123456789abcdef" for c in audio_id):
            raise ValueError("Invalid audio identifier")
        path = self.directory / f"{audio_id}.wav"
        if not path.exists():
            response = httpx.get(self.base_url + f"/audio/{audio_id}.wav", timeout=30)
            response.raise_for_status()
            path.write_bytes(response.content)
        return path


class TelegramWireFixture:
    def __init__(self, audio):
        self.audio = audio
        self.updates = asyncio.Queue()
        self.calls = []
        self.sent = []

    async def handle(self, request):
        method = request.url.path.rsplit("/", 1)[-1]
        self.calls.append(method)
        if method == "getMe":
            result = {"id": 900, "is_bot": True, "username": "fixture_bot"}
        elif method == "getWebhookInfo":
            result = {"url": ""}
        elif method == "getUpdates":
            result = await self.updates.get()
        elif method == "getFile":
            result = {"file_path": "voice/input.ogg", "file_size": len(self.audio)}
        elif method == "input.ogg":
            return httpx.Response(200, content=self.audio, headers={"content-type": "audio/ogg"})
        elif method == "sendVoice":
            envelope = (
                b"Content-Type: "
                + request.headers["content-type"].encode()
                + b"\r\nMIME-Version: 1.0\r\n\r\n"
                + await request.aread()
            )
            message = BytesParser(policy=policy.default).parsebytes(envelope)
            fields = {}
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                fields[name] = part.get_payload(decode=True)
            self.sent.append(fields)
            result = {"message_id": 999}
        else:
            raise AssertionError(f"Unexpected fixture request: {method}")
        return httpx.Response(200, json={"ok": True, "result": result})


async def until(predicate, timeout=240):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.05)


async def verify(base_url):
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="loudtalk-real-pipeline-") as folder:
        directory = Path(folder)
        workspace = directory / "agent-workspace"
        workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        speech = LocalSpeech(base_url, directory)
        text = (
            "To jest test rozmowy głosowej. Odpowiedz dokładnie: "
            "Słyszę twoją wiadomość i mogę odpowiadać głosem po polsku."
        )
        source = await asyncio.to_thread(speech.synthesize, text, "gosia")
        source_path = await asyncio.to_thread(speech.resolve_audio, source["audio_id"])
        input_opus = await encode_voice(source_path, "opus")
        wire = TelegramWireFixture(input_opus.read_bytes())
        input_opus.unlink()

        def factory(config):
            return TelegramAdapter(
                config,
                client_factory=lambda **options: httpx.AsyncClient(
                    transport=httpx.MockTransport(wire.handle),
                    **options,
                ),
            )

        store = Store(directory / "state")
        agent = store.save_agent(
            {
                "name": "Real Codex pipeline check",
                "kind": "command",
                "command_id": "codex",
                "working_directory": str(workspace),
                "voice": "gosia",
            }
        )
        manager = ChannelManager(directory / "state", store, speech, factory)
        channel = manager.create(
            {
                "platform": "telegram",
                "name": "Explicit protocol fixture",
                "agent_id": agent["id"],
                "secrets": {"bot_token": "123:fixture_only"},
            }
        )

        def update(identifier):
            return {
                "update_id": identifier,
                "message": {
                    "message_id": identifier,
                    "message_thread_id": 71,
                    "from": {"id": 501, "is_bot": False},
                    "chat": {"id": -100700, "type": "supergroup"},
                    "voice": {
                        "file_id": "fixture-file",
                        "mime_type": "audio/ogg",
                        "file_size": len(wire.audio),
                    },
                },
            }

        await manager.start()
        try:
            await manager.enable(channel["id"])
            await wire.updates.put([update(1)])
            await until(lambda: bool(manager.pairings()), timeout=10)
            assert "getFile" not in wire.calls
            assert not wire.sent
            manager.approve(channel["id"], manager.pairings()[0]["id"])
            await wire.updates.put([update(2), update(2)])
            await until(
                lambda: (
                    manager.events()
                    and manager.events()[0]["status"] in {"sent", "error", "uncertain"}
                )
            )
            event = manager.events()[0]
            assert event["status"] == "sent", event
            assert len(manager.events()) == len(wire.sent) == wire.calls.count("getFile") == 1
            sent = wire.sent[0]
            assert sent["chat_id"] == b"-100700"
            assert sent["message_thread_id"] == b"71"
            assert json.loads(sent["reply_parameters"])["message_id"] == 2
            assert sent["voice"].startswith(b"OggS")
            output_path = directory / "reply.ogg"
            output_path.write_bytes(sent["voice"])
            probe = json.loads(
                subprocess.check_output(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_streams",
                        "-show_format",
                        "-of",
                        "json",
                        str(output_path),
                    ]
                )
            )
            stream = probe["streams"][0]
            assert stream["codec_name"] == "opus"
            assert stream["sample_rate"] == "48000"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(output_path),
                    "-f",
                    "null",
                    "-",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            recognized = await asyncio.to_thread(speech.transcribe, output_path)
            assert "wiadomość" in event["transcript"].lower(), event["transcript"]
            assert "głosem" in recognized["text"].lower(), recognized["text"]
            assert "polsku" in recognized["text"].lower(), recognized["text"]
            return {
                "passed": True,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "scope": "Real Loudkit input, real Parakeet, production queue and Codex dispatch, "
                "real Loudkit response, production Telegram multipart/Opus and real STT recheck",
                "exclusions": ["Telegram network/account and remote delivery", "Human listening"],
                "messenger_transport": "Explicit in-memory HTTP fixture; no Telegram traffic",
                "agent": "Codex CLI",
                "input_text": text,
                "transcript": event["transcript"],
                "agent_reply": event["reply"],
                "response_transcript": recognized["text"],
                "codec": stream["codec_name"],
                "sample_rate": stream["sample_rate"],
                "duration_seconds": float(probe["format"]["duration"]),
                "outgoing_audio_sha256": hashlib.sha256(sent["voice"]).hexdigest(),
                "outgoing_bytes": len(sent["voice"]),
                "outgoing_count": len(wire.sent),
                "duplicate_input_ignored": True,
                "unpaired_input_not_downloaded": True,
                "same_chat_thread_and_reply_target": True,
                "elapsed_seconds": round(time.monotonic() - started, 2),
            }
        finally:
            await manager.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # This opt-in verifier is intentionally loopback-only and accepts no secrets.
    url = httpx.URL(args.base_url)
    if url.scheme != "http" or url.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Use the running local LoudTalk server on loopback")
    report = asyncio.run(verify(args.base_url.rstrip("/")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
