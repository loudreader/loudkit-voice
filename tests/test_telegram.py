from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from loudtalk.channels import telegram
from loudtalk.channels.base import ChannelError

TOKEN = "123456:test_token_not_a_real_secret"


def config():
    return {"id": "telegram-test", "secrets": {"bot_token": TOKEN}, "settings": {}}


def update(number=91, *, chat_id=-100123, message_id=44):
    return {
        "update_id": number,
        "message": {
            "message_id": message_id,
            "message_thread_id": 7,
            "from": {"id": 222, "is_bot": False},
            "chat": {"id": chat_id},
            "voice": {"file_id": "voice-file", "file_size": 8, "duration": 2},
        },
    }


def adapter(handler):
    transport = httpx.MockTransport(handler)
    return telegram.TelegramChannel(
        config(), client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)
    )


def ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


def test_voice_is_normalized_without_network_and_events_do_not_collide_across_chats():
    channel = adapter(lambda request: pytest.fail("Parsing must not download audio"))
    event = channel.parse_update(update())
    assert event.channel_id == "telegram-test"
    assert (event.event_id, event.chat_id, event.sender_id, event.thread_id) == (
        "91", "-100123", "222", "7"
    )
    assert event.audio_ref["file_id"] == "voice-file"
    assert event.filename == "voice.ogg"
    assert channel.parse_update(update(92, chat_id=1)).event_id != event.event_id


def test_ignores_bots_edits_non_audio_and_anonymous_senders():
    channel = adapter(lambda request: pytest.fail("No network expected"))
    message = update()
    message["message"]["from"]["is_bot"] = True
    assert channel.parse_update(message) is None
    assert channel.parse_update({"update_id": 91, "edited_message": update()["message"]}) is None
    message = update()
    del message["message"]["voice"]
    message["message"]["text"] = "hi"
    assert channel.parse_update(message) is None
    message = update()
    del message["message"]["from"]
    assert channel.parse_update(message) is None


def test_audio_documents_and_original_filename_are_supported():
    channel = adapter(lambda request: pytest.fail("No network expected"))
    message = update()
    del message["message"]["voice"]
    message["message"]["document"] = {
        "file_id": "file", "mime_type": "audio/mpeg", "file_name": "../../hello.mp3"
    }
    assert channel.parse_update(message).filename == "hello.mp3"
    message["message"]["document"]["mime_type"] = "application/pdf"
    assert channel.parse_update(message) is None


async def test_check_connection_is_read_only_and_returns_verified_bot():
    methods = []

    def handler(request):
        methods.append(request.url.path.rsplit("/", 1)[1])
        if methods[-1] == "getMe":
            return ok({"id": 123456, "is_bot": True, "username": "voice_test_bot"})
        assert methods[-1] == "getWebhookInfo"
        return ok({"url": "", "pending_update_count": 4})

    result = await adapter(handler).check_connection()
    assert result == {"ok": True, "identity": "@voice_test_bot", "bot_id": "123456"}
    assert methods == ["getMe", "getWebhookInfo"]


async def test_existing_webhook_is_rejected_and_not_deleted_or_polled():
    methods = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[1]
        methods.append(method)
        if method == "getMe":
            return ok({"id": 123456, "is_bot": True})
        assert method == "getWebhookInfo"
        return ok({"url": "https://existing-agent.example/token-secret"})

    with pytest.raises(ChannelError, match="webhook") as error:
        await adapter(handler).run(lambda event: None)
    assert "token-secret" not in str(error.value)
    assert methods == ["getMe", "getWebhookInfo"]


async def test_poller_acknowledges_only_after_durable_acceptance():
    accepted, offsets = [], []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[1]
        if method == "getMe":
            return ok({"id": 123456, "is_bot": True})
        if method == "getWebhookInfo":
            return ok({"url": ""})
        assert method == "getUpdates"
        payload = json.loads(request.content)
        assert payload["allowed_updates"] == ["message"]
        offsets.append(payload.get("offset"))
        if len(offsets) == 1:
            return ok([update()])
        assert accepted == ["91"]
        raise asyncio.CancelledError

    async def accept(event):
        accepted.append(event.event_id)
        return True

    with pytest.raises(asyncio.CancelledError):
        await adapter(handler).run(accept)
    assert offsets == [None, 92]


@pytest.mark.parametrize("failure", [False, RuntimeError("disk write failed")])
async def test_failed_enqueue_never_moves_telegram_cursor(failure):
    calls = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[1]
        calls.append(method)
        if method == "getMe":
            return ok({"id": 123456, "is_bot": True})
        if method == "getWebhookInfo":
            return ok({"url": ""})
        return ok([update(), update(92)])

    async def accept(event):
        if isinstance(failure, Exception):
            raise failure
        return failure

    channel = adapter(handler)
    with pytest.raises((ChannelError, RuntimeError)):
        await channel.run(accept)
    assert channel._offset is None
    assert calls.count("getUpdates") == 1


async def test_file_id_resolves_to_fixed_telegram_host_and_audio_download():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        assert request.url.host == "api.telegram.org"
        if request.url.path.endswith("getFile"):
            assert json.loads(request.content) == {"file_id": "voice-file"}
            return ok({"file_path": "voice/file_7.oga", "file_size": 8})
        assert request.method == "GET"
        assert request.url.path == f"/file/bot{TOKEN}/voice/file_7.oga"
        return httpx.Response(200, content=b"ogg-data", headers={"content-type": "audio/ogg"})

    channel = adapter(handler)
    data, filename = await channel.download_audio(channel.parse_update(update()))
    assert (data, filename) == (b"ogg-data", "voice.ogg")
    assert len(paths) == 2


@pytest.mark.parametrize("path", [
    "https://evil.example/audio", "//evil.example/audio", "../secret", "audio/../secret",
    "voice/%2e%2e/secret", "voice/file?token=bad", "voice\\file", "/voice/file",
])
async def test_rejects_untrusted_download_path_without_making_download_request(path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return ok({"file_path": path})

    channel = adapter(handler)
    with pytest.raises(ChannelError, match="adres"):
        await channel.download_audio(channel.parse_update(update()))
    assert len(calls) == 1


async def test_download_does_not_follow_redirects():
    def handler(request):
        if request.url.path.endswith("getFile"):
            return ok({"file_path": "voice/file.oga"})
        assert request.url.host == "api.telegram.org"
        return httpx.Response(302, headers={"location": "https://evil.example/secret"})

    channel = adapter(handler)
    with pytest.raises(ChannelError, match="pobrać"):
        await channel.download_audio(channel.parse_update(update()))


@pytest.mark.parametrize("declared", [True, False])
async def test_download_is_bounded_even_without_content_length(monkeypatch, declared):
    monkeypatch.setattr(telegram, "MAX_DOWNLOAD_BYTES", 8)

    def handler(request):
        if request.url.path.endswith("getFile"):
            return ok({"file_path": "voice/file.oga"})
        response = httpx.Response(200, content=b"way-too-much-audio")
        if not declared:
            response.headers.pop("content-length", None)
        return response

    channel = adapter(handler)
    with pytest.raises(ChannelError, match="limit"):
        await channel.download_audio(channel.parse_update(update()))


async def test_oversized_metadata_blocks_download_before_api_call():
    channel = adapter(lambda request: pytest.fail("Oversized attachment must not download"))
    event = channel.parse_update(update())
    event.audio_ref["file_size"] = telegram.MAX_DOWNLOAD_BYTES + 1
    with pytest.raises(ChannelError, match="20 MB"):
        await channel.download_audio(event)


@pytest.mark.parametrize("status", [401, 403, 409, 429, 500])
async def test_errors_are_user_safe_and_do_not_leak_token(status):
    def handler(request):
        return httpx.Response(status, json={"description": f"request contained {TOKEN}"})

    with pytest.raises(ChannelError) as error:
        await adapter(handler).check_connection()
    assert TOKEN not in str(error.value)


async def test_network_error_hides_credential_embedded_url():
    def handler(request):
        raise httpx.ConnectError(str(request.url), request=request)

    with pytest.raises(ChannelError) as error:
        await adapter(handler).check_connection()
    assert TOKEN not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize("status", [200, 500])
async def test_send_voice_uses_native_ogg_reply_in_same_topic_and_cleans_temp(
    monkeypatch, tmp_path, status
):
    source = tmp_path / "original.wav"
    source.write_bytes(b"original")
    converted = tmp_path / "generated.ogg"
    converted.write_bytes(b"Opus-voice-content")

    async def encode(path, *, format):
        assert path == source and format == "opus"
        return converted

    monkeypatch.setattr(telegram, "encode_voice", encode)

    async def handler(request):
        assert request.url.path.endswith("/sendVoice")
        payload = await request.aread()
        assert b'name="voice"; filename="reply.ogg"' in payload
        assert b"Content-Type: audio/ogg" in payload
        assert b"Opus-voice-content" in payload
        assert b'name="chat_id"\r\n\r\n-100123' in payload
        assert b'name="message_thread_id"\r\n\r\n7' in payload
        assert b'name="duration"\r\n\r\n3' in payload
        assert b'"message_id": 44' in payload
        assert b'name="text"' not in payload
        return ok({"message_id": 45, "voice": {}}) if status == 200 else httpx.Response(status)

    channel = adapter(handler)
    if status == 200:
        result = await channel.send_voice(channel.parse_update(update()), source, "reply", 2.5)
        assert result == {"message_id": "45", "native_voice": True}
    else:
        with pytest.raises(ChannelError):
            await channel.send_voice(channel.parse_update(update()), source, "reply", 2.5)
    assert not converted.exists()
    assert source.read_bytes() == b"original"


async def test_invalid_token_cannot_escape_bot_url():
    bad_config = config()
    bad_config["secrets"]["bot_token"] = "123:secret/path?query=value"
    channel = telegram.TelegramChannel(bad_config)
    with pytest.raises(ChannelError, match="BotFather"):
        await channel.check_connection()
