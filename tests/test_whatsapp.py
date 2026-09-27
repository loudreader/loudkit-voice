"""WhatsApp contract checks with mocked HTTP; no real provider delivery is implied."""

import hashlib
import hmac
import json

import httpx
import pytest

from loudtalk.channels import whatsapp
from loudtalk.channels.base import ChannelError
from loudtalk.channels.whatsapp import WhatsAppAdapter


@pytest.fixture
def adapter():
    return WhatsAppAdapter({
        "id": "wa", "secrets": {"access_token": "test-access", "app_secret": "test-secret",
                                  "verify_token": "test-verify"},
        "settings": {"phone_number_id": "12345"},
    })


def payload(**message_changes):
    message = {"id": "wamid.test", "from": "48123456789", "type": "audio",
               "audio": {"id": "98765", "mime_type": "audio/ogg; codecs=opus", "voice": True}}
    message.update(message_changes)
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{
        "field": "messages", "value": {"metadata": {"phone_number_id": "12345"},
                                         "messages": [message]},
    }]}]}


def mock_http(monkeypatch, handler):
    cls = httpx.AsyncClient
    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", lambda **kw: cls(
        transport=httpx.MockTransport(handler), **kw,
    ))


def test_signature_and_verification(adapter):
    raw = json.dumps(payload()).encode()
    signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
    assert adapter.verify_webhook(raw, {"X-Hub-Signature-256": signature}, {}) is None
    with pytest.raises(ChannelError, match="signature"):
        adapter.verify_webhook(raw + b" ", {"x-hub-signature-256": signature}, {})
    with pytest.raises(ChannelError, match="signature"):
        adapter.verify_webhook(raw, {}, {})
    assert adapter.verify_webhook(b"", {}, {"hub.mode": "subscribe",
        "hub.verify_token": "test-verify", "hub.challenge": "123"}) == "123"
    with pytest.raises(ChannelError, match="token"):
        adapter.verify_webhook(b"", {}, {"hub.mode": "subscribe",
            "hub.verify_token": "bad", "hub.challenge": "123"})


def test_audio_only_correct_number_and_echo_suppression(adapter):
    events = adapter.parse_events(payload())
    assert len(events) == 1
    assert events[0].chat_id == events[0].sender_id == "48123456789"
    assert events[0].audio_ref["media_id"] == "98765"
    for replacement in ({"type": "text"}, {"is_echo": True}, {"from_me": True}):
        assert adapter.parse_events(payload(**replacement)) == []
    value = payload()
    value["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "other"
    assert adapter.parse_events(value) == []
    assert adapter.parse_events({"object": "whatsapp_business_account", "entry": []}) == []


async def test_connection_is_read_only(monkeypatch, adapter):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "12345", "verified_name": "Test bot"})
    mock_http(monkeypatch, handler)
    assert (await adapter.check_connection())["ok"]
    assert len(calls) == 1 and calls[0].method == "GET"
    assert calls[0].headers["authorization"] == "Bearer test-access"
    assert "test-access" not in str(calls[0].url)


async def test_download_fetches_authenticated_metadata_then_voice(monkeypatch, adapter):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer test-access"
        if request.url.host == "graph.facebook.com":
            assert request.url.params["phone_number_id"] == "12345"
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/voice",
                "mime_type": "audio/ogg; codecs=opus", "file_size": 5})
        return httpx.Response(200, content=b"audio")
    mock_http(monkeypatch, handler)
    assert await adapter.download_audio(adapter.parse_events(payload())[0]) == (b"audio", "voice.ogg")
    assert len(calls) == 2


@pytest.mark.parametrize("url", ["https://evil.example/x", "http://lookaside.fbsbx.com/x",
    "https://lookaside.fbsbx.com.evil.example/x", "https://lookaside.fbsbx.com:444/x",
    "https://token@lookaside.fbsbx.com/x"])
async def test_download_rejects_untrusted_vendor_url(monkeypatch, adapter, url):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"url": url, "mime_type": "audio/ogg"})
    mock_http(monkeypatch, handler)
    with pytest.raises(ChannelError, match="host"):
        await adapter.download_audio(adapter.parse_events(payload())[0])
    assert len(calls) == 1


async def test_download_bounds_actual_stream_and_refuses_redirect(monkeypatch, adapter):
    monkeypatch.setattr(whatsapp, "MAX_AUDIO_BYTES", 4)
    def handler(request):
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/voice",
                                           "mime_type": "audio/ogg"})
        return httpx.Response(200, content=b"toolong")
    mock_http(monkeypatch, handler)
    with pytest.raises(ChannelError, match="exceeds"):
        await adapter.download_audio(adapter.parse_events(payload())[0])


async def test_outbound_is_native_voice_reply_and_cleans_export(monkeypatch, adapter, tmp_path):
    encoded = tmp_path / "export.ogg"
    encoded.write_bytes(b"OggS-opus-data")
    async def encode(path, format):
        assert format == "opus"
        return encoded
    monkeypatch.setattr(whatsapp, "encode_voice", encode)
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/media"):
            assert b"audio/ogg; codecs=opus" in request.content
            assert b"OggS-opus-data" in request.content
            return httpx.Response(200, json={"id": "456"})
        body = json.loads(request.content)
        assert body["audio"] == {"id": "456", "voice": True}
        assert body["to"] == "48123456789"
        assert body["context"]["message_id"] == "wamid.test"
        return httpx.Response(200, json={"messages": [{"id": "wamid.reply"}]})
    mock_http(monkeypatch, handler)
    result = await adapter.send_voice(adapter.parse_events(payload())[0], tmp_path / "in.wav", "Hi", 2)
    assert result["native_voice"] and result["message_id"] == "wamid.reply"
    assert len(calls) == 2 and not encoded.exists()


async def test_error_never_exposes_provider_body_or_token(monkeypatch, adapter):
    mock_http(monkeypatch, lambda request: httpx.Response(401, json={
        "error": {"message": "secret-access-token test-access"},
    }))
    with pytest.raises(ChannelError) as error:
        await adapter.check_connection()
    assert "test-access" not in str(error.value)
    assert "secret-access-token" not in str(error.value)


async def test_redirect_is_not_followed_with_bearer(monkeypatch, adapter):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/voice",
                                           "mime_type": "audio/ogg"})
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})
    mock_http(monkeypatch, handler)
    with pytest.raises(ChannelError, match="download"):
        await adapter.download_audio(adapter.parse_events(payload())[0])
    assert len(calls) == 2
    assert all(request.url.host != "evil.example" for request in calls)


async def test_failed_upload_also_removes_encoded_file(monkeypatch, adapter, tmp_path):
    encoded = tmp_path / "export.ogg"
    encoded.write_bytes(b"OggS")
    async def encode(path, format):
        return encoded
    monkeypatch.setattr(whatsapp, "encode_voice", encode)
    mock_http(monkeypatch, lambda request: httpx.Response(500))
    with pytest.raises(ChannelError):
        await adapter.send_voice(adapter.parse_events(payload())[0], tmp_path / "input.wav", "", 1)
    assert not encoded.exists()
