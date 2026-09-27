"""Protocol tests: no Discord account, token lookup, or real messages required."""

import base64
import json
import math
import struct
import sys
import types
import wave
from email.parser import BytesParser
from email.policy import default

import httpx
import pytest

from loudtalk.channels.base import ChannelError
from loudtalk.channels.discord import DiscordConnector, _voice_metadata


def connector(handler=None, **settings):
    factory = None
    if handler:
        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
    return DiscordConnector(
        {"id": "discord-test", "secrets": {"bot_token": "unit-test-token"}, "settings": settings},
        client_factory=factory,
    )


def message(**changes):
    result = {
        "id": "1234", "channel_id": "2345", "author": {"id": "3456", "bot": False},
        "attachments": [{
            "id": "4567", "filename": "voice-message.ogg", "content_type": "audio/ogg",
            "size": 10, "url": "https://cdn.discordapp.com/attachments/2345/4567/voice.ogg?ex=signed",
        }],
    }
    result.update(changes)
    return result


def make_wav(path, *, seconds=1):
    rate = 8000
    samples = [
        int((0.1 if i < rate // 2 else 0.8) * 32767 * math.sin(i * 2 * math.pi * 200 / rate))
        for i in range(rate * seconds)
    ]
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


async def test_check_connection_is_only_authenticated_read():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url) == "https://discord.com/api/v10/users/@me"
        assert request.headers["authorization"] == "Bot unit-test-token"
        return httpx.Response(200, json={"id": "42", "username": "Voice bot", "bot": True})

    result = await connector(handler).check_connection()
    assert result == {"ok": True, "identity": "Voice bot", "bot_id": "42"}
    assert len(requests) == 1


@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_api_failures_do_not_leak_token_or_service_body(status):
    def handler(request):
        return httpx.Response(status, json={"message": "unit-test-token private-response"})

    with pytest.raises(ChannelError) as error:
        await connector(handler).check_connection()
    assert "unit-test-token" not in str(error.value)
    assert "private-response" not in str(error.value)


async def test_personal_accounts_are_rejected():
    with pytest.raises(ChannelError, match="token bota"):
        await connector(lambda _: httpx.Response(200, json={"id": "42", "bot": False})).check_connection()


def test_normalization_preserves_thread_sender_and_unique_attachment_ids():
    incoming = message(is_thread=True)
    incoming["attachments"].append({**incoming["attachments"][0], "id": "4568"})
    events = connector().parse_message(incoming)
    assert [event.event_id for event in events] == ["1234:4567", "1234:4568"]
    assert events[0].channel_id == "discord-test"
    assert events[0].chat_id == events[0].thread_id == "2345"
    assert events[0].sender_id == "3456"
    assert events[0].audio_ref["message_id"] == "1234"


@pytest.mark.parametrize("changes", [
    {"author": {"id": "3456", "bot": True}},
    {"webhook_id": "42"},
    {"attachments": []},
    {"author": {"id": "../../evil"}},
    {"channel_id": "2345/messages?other=1"},
    {"attachments": [{"id": "4", "filename": "script.py", "content_type": "text/plain"}]},
    {"author": "invalid"},
    {"attachments": "invalid"},
    {"attachments": [None]},
])
def test_non_audio_bots_and_invalid_events_are_ignored(changes):
    assert connector().parse_message(message(**changes)) == []


async def test_download_is_bounded_and_does_not_send_token():
    def handler(request):
        assert request.url.host == "cdn.discordapp.com"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"OggS-audio")

    bridge = connector(handler)
    event = bridge.parse_message(message())[0]
    assert await bridge.download_audio(event) == (b"OggS-audio", "voice-message.ogg")


@pytest.mark.parametrize("url", [
    "http://cdn.discordapp.com/attachments/a",
    "https://cdn.discordapp.com.evil.test/attachments/a",
    "https://127.0.0.1/attachments/a",
    "https://cdn.discordapp.com@evil.test/attachments/a",
    "https://user:password@cdn.discordapp.com/attachments/a",
    "https://cdn.discordapp.com:8443/attachments/a",
    "https://discord.com/api/v10/users/@me",
    "https://cdn.discordapp.com/unexpected/a",
])
async def test_download_rejects_external_addresses_before_network(url):
    def handler(request):
        pytest.fail("unsafe URL reached transport")

    bridge = connector(handler)
    event = bridge.parse_message(message())[0]
    event.audio_ref["url"] = url
    with pytest.raises(ChannelError, match="adres pobierania"):
        await bridge.download_audio(event)


async def test_download_does_not_follow_redirects():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://evil.test/audio"})

    bridge = connector(handler)
    with pytest.raises(ChannelError):
        await bridge.download_audio(bridge.parse_message(message())[0])
    assert len(requests) == 1


@pytest.mark.parametrize("declared", [True, False])
async def test_download_enforces_limit_for_declared_and_streamed_body(monkeypatch, declared):
    monkeypatch.setattr("loudtalk.channels.discord.MAX_AUDIO_BYTES", 12)

    class AudioStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"12345678"
            yield b"12345678"

    def handler(request):
        headers = {"content-length": "16"} if declared else {}
        return httpx.Response(200, headers=headers, stream=AudioStream())

    bridge = connector(handler)
    with pytest.raises(ChannelError, match="25 MiB"):
        await bridge.download_audio(bridge.parse_message(message())[0])


def test_waveform_is_measured_from_audio_and_has_real_duration(tmp_path):
    path = make_wav(tmp_path / "reply.wav")
    duration, waveform = _voice_metadata(path)
    samples = base64.b64decode(waveform)
    assert duration == 1
    assert len(samples) == 10
    assert 20 < samples[0] < 30
    assert 190 < samples[-1] < 220


def test_waveform_long_audio_is_downsampled_to_256_points(tmp_path):
    path = make_wav(tmp_path / "long.wav", seconds=30)
    duration, waveform = _voice_metadata(path)
    assert duration == 30
    assert len(base64.b64decode(waveform)) == 256


async def test_send_is_native_voice_multipart_to_original_thread(tmp_path, monkeypatch):
    wav = make_wav(tmp_path / "reply.wav")
    encoded = tmp_path / "reply.opus.ogg"
    encoded.write_bytes(b"OggS-encoded-reply")
    seen = []

    async def encode(path, format):
        assert path == wav and format == "opus"
        return encoded

    def handler(request):
        seen.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://discord.com/api/v10/channels/9999/messages"
        assert request.headers["authorization"] == "Bot unit-test-token"
        parts = BytesParser(policy=default).parsebytes(
            f'Content-Type: {request.headers["content-type"]}\r\n\r\n'.encode() + request.content
        )
        form = {
            part.get_param("name", header="content-disposition"): part
            for part in parts.iter_parts()
        }
        payload = json.loads(form["payload_json"].get_payload(decode=True))
        assert payload["flags"] == 8192
        assert payload["enforce_nonce"] is True and len(payload["nonce"]) == 25
        assert payload["allowed_mentions"]["parse"] == []
        assert "content" not in payload and "tts" not in payload
        attachment = payload["attachments"][0]
        assert attachment["id"] == 0
        assert attachment["duration_secs"] == 1
        assert len(base64.b64decode(attachment["waveform"])) == 10
        assert form["files[0]"].get_content_type() == "audio/ogg"
        assert form["files[0]"].get_payload(decode=True) == b"OggS-encoded-reply"
        return httpx.Response(200, json={"id": "7777", "channel_id": "9999", "flags": 8192})

    monkeypatch.setattr("loudtalk.channels.discord.encode_voice", encode)
    bridge = connector(handler)
    event = bridge.parse_message(message(channel_id="9999", is_thread=True))[0]
    result = await bridge.send_voice(event, wav, "@everyone do not ping", duration=900)
    assert result == {"message_id": "7777", "chat_id": "9999", "native_voice": True}
    assert len(seen) == 1
    assert wav.exists() and not encoded.exists()


async def test_run_queues_incoming_audio_and_does_not_enable_privileged_intent_by_default(monkeypatch):
    instances = []
    queued = []

    class Intents:
        @classmethod
        def none(cls):
            return types.SimpleNamespace(dm_messages=False, guilds=False,
                                         guild_messages=False, message_content=False)

    class Thread:
        id = 2345

    class Client:
        def __init__(self, *, intents):
            self.intents = intents
            self.closed = False
            instances.append(self)

        def event(self, callback):
            self.callback = callback
            return callback

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

        async def close(self):
            self.closed = True

        async def start(self, token, *, reconnect):
            assert token == "unit-test-token" and reconnect is True
            payload = message()
            await self.callback(types.SimpleNamespace(
                id=1234, channel=Thread(), author=types.SimpleNamespace(id=3456, bot=False),
                webhook_id=None,
                attachments=[types.SimpleNamespace(to_dict=lambda: payload["attachments"][0])],
            ))

    monkeypatch.setitem(sys.modules, "discord", types.SimpleNamespace(
        Intents=Intents, Client=Client, Thread=Thread,
    ))

    async def accept(event):
        queued.append(event)
        return True

    await connector().run(accept)
    assert len(queued) == 1 and queued[0].thread_id == "2345"
    assert instances[-1].intents.dm_messages
    assert not instances[-1].intents.message_content
    assert instances[-1].closed
    await connector(guild_messages=True).run(accept)
    assert instances[-1].intents.message_content and instances[-1].intents.guild_messages

    async def reject(event):
        return False

    with pytest.raises(ChannelError, match="kolejce"):
        await connector().run(reject)
    assert instances[-1].closed


async def test_oversized_audio_becomes_visible_error_after_normalization():
    def handler(request):
        pytest.fail("oversized file reached transport")

    bridge = connector(handler)
    payload = message()
    payload["attachments"][0]["size"] = 26 * 1024 * 1024
    event = bridge.parse_message(payload)[0]
    with pytest.raises(ChannelError, match="25 MiB"):
        await bridge.download_audio(event)


async def test_api_response_is_bounded(monkeypatch):
    monkeypatch.setattr("loudtalk.channels.discord.MAX_API_BYTES", 12)
    with pytest.raises(ChannelError, match="zbyt dużą"):
        await connector(lambda _: httpx.Response(200, content=b"x" * 13)).check_connection()
