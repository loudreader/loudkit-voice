"""BlueBubbles protocol tests. No Apple account or live bridge is contacted."""

from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from loudtalk.channels.base import ChannelError
from loudtalk.channels.imessage import IMessageAdapter

CONFIG = {
    "id": "imessage-test",
    "secrets": {"password": "bridge-password", "webhook_secret": "w" * 40},
    "settings": {"server_url": "http://127.0.0.1:1234"},
}
# Shape follows upstream MessageSerializer / AttachmentSerializer, wrapped by
# WebhookService.dispatch. This is a fabricated protocol fixture, not user data.
VOICE_EVENT = {
    "type": "new-message",
    "data": {
        "guid": "11111111-2222-3333-4444-555555555555",
        "isFromMe": False,
        "isAudioMessage": True,
        "handle": {"address": "+48111000222", "service": "iMessage"},
        "chats": [{"guid": "iMessage;-;+48111000222", "style": 45}],
        "attachments": [{
            "guid": "at_0_11111111-2222-3333-4444-555555555555",
            "transferName": "Audio Message.caf",
            "mimeType": "audio/x-caf",
            "uti": "com.apple.coreaudio-format",
            "totalBytes": 1024,
            "isOutgoing": False,
        }],
    },
}


def adapter(handler=None, **settings):
    config = deepcopy(CONFIG)
    config["settings"].update(settings)
    transport = httpx.MockTransport(handler) if handler else None
    return IMessageAdapter(config, transport=transport)


def success(data):
    return httpx.Response(200, json={"status": 200, "message": "Success", "data": data})


def test_webhook_url_token_is_required_and_password_is_not_webhook_auth():
    bridge = adapter()
    assert bridge.verify_webhook(b"{}", {}, {"token": "w" * 40}) is None
    for query in ({}, {"password": "bridge-password"}, {"token": "bridge-password"}):
        with pytest.raises(ChannelError):
            bridge.verify_webhook(b"{}", {"Authorization": "bridge-password"}, query)
    config = deepcopy(CONFIG)
    config["secrets"]["webhook_secret"] = ""
    with pytest.raises(ChannelError):
        IMessageAdapter(config).verify_webhook(b"{}", {}, {"token": ""})


def test_caf_voice_event_preserves_sender_chat_and_attachment_identity():
    event, = adapter().parse_events(VOICE_EVENT)
    assert event.sender_id == "+48111000222"
    assert event.chat_id == "iMessage;-;+48111000222"
    assert event.audio_ref["guid"] == VOICE_EVENT["data"]["attachments"][0]["guid"]
    assert event.filename == "Audio Message.caf"
    assert event.event_id.startswith(VOICE_EVENT["data"]["guid"] + ":")


def test_notification_shaped_webhook_does_not_require_private_api_voice_flags():
    # handleNewMessage dispatches the notification serializer to webhooks; its
    # compact shape omits isAudioMessage and attachment.isOutgoing.
    payload = deepcopy(VOICE_EVENT)
    payload["data"].pop("isAudioMessage")
    payload["data"]["attachments"][0].pop("isOutgoing")
    event, = adapter().parse_events(payload)
    assert event.filename == "Audio Message.caf"


@pytest.mark.parametrize("change", [
    {"isFromMe": True},
    {"isFromMe": None},
    {"handle": None},
    {"chats": []},
    {"chats": [{"guid": "iMessage;-;+1"}, {"guid": "iMessage;-;+2"}]},
    {"chats": [{"guid": "SMS;-;+48111000222"}]},
])
def test_ignores_own_unknown_sender_ambiguous_destination_and_sms(change):
    payload = deepcopy(VOICE_EVENT)
    payload["data"].update(change)
    assert adapter().parse_events(payload) == []


@pytest.mark.parametrize("payload", [None, [], {}, {"type": "updated-message"},
                                           {"type": "new-message", "data": "bad"}])
def test_non_message_or_malformed_events_are_ignored(payload):
    assert adapter().parse_events(payload) == []


def test_image_and_unknown_file_do_not_become_voice_commands():
    payload = deepcopy(VOICE_EVENT)
    payload["data"]["isAudioMessage"] = False
    payload["data"]["attachments"] = [
        {"guid": "image-guid", "mimeType": "image/png", "transferName": "voice.m4a"},
        {"guid": "empty-guid"},
        {"guid": "invalid-uti", "uti": {"unexpected": "object"}},
        {"guid": "pdf-guid", "mimeType": "application/pdf", "transferName": "file.pdf"},
    ]
    assert adapter().parse_events(payload) == []


def test_audio_attachment_can_be_a_command_without_native_voice_flag():
    payload = deepcopy(VOICE_EVENT)
    payload["data"]["isAudioMessage"] = False
    payload["data"]["attachments"] = [{
        "guid": "audio-guid", "mimeType": "application/octet-stream",
        "transferName": "../../recording.m4a",
    }]
    event, = adapter().parse_events(payload)
    assert event.filename == "recording.m4a"


def test_duplicate_attachments_emit_one_event_and_urls_are_not_media_references():
    payload = deepcopy(VOICE_EVENT)
    attachment = payload["data"]["attachments"][0]
    payload["data"]["attachments"].extend([
        attachment,
        {**attachment, "guid": "https://attacker.invalid/audio"},
        {**attachment, "guid": "../../server"},
    ])
    assert len(adapter().parse_events(payload)) == 1


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1234", "http://localhost:1234/", "http://192.168.1.10:1234",
    "http://10.0.0.1:1234", "http://172.16.0.1:1234", "http://[::1]:1234",
    "https://bridge.example.org", "https://bridge.example.org/bluebubbles",
])
def test_user_configured_local_or_https_bridge_urls(url):
    assert adapter(server_url=url).server_url == url.rstrip("/")


@pytest.mark.parametrize("url", [
    "http://bridge.example.org", "http://8.8.8.8:1234", "http://169.254.169.254",
    "http://127.0.0.1.evil.test", "file:///tmp/bridge", "https://user:secret@example.org",
    "https://example.org?password=secret", "https://example.org/#fragment",
    "https://example.org/../other", "https://example.org/%2e%2e/other",
])
def test_rejects_insecure_public_or_ambiguous_bridge_urls(url):
    with pytest.raises(ChannelError):
        adapter(server_url=url)


async def test_connection_checks_authenticated_server_metadata_and_no_private_data():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/api/v1/server"
        assert request.url.params["password"] == "bridge-password"
        return success({
            "server_version": "1.9.9", "private_api": True, "helper_connected": True,
            "detected_icloud": "private@example.invalid",
        })

    status = await adapter(handler).check_connection()
    assert status["ok"] is True
    assert status["native_audio_available"] is True
    assert "private@example.invalid" not in str(status)
    assert len(requests) == 1


async def test_original_caf_download_uses_only_configured_bridge_and_password_query():
    def handler(request):
        assert request.url.host == "127.0.0.1"
        assert request.url.path.endswith("/" + VOICE_EVENT["data"]["attachments"][0]["guid"] + "/download")
        assert dict(request.url.params) == {
            "password": "bridge-password", "original": "true", "force": "false"
        }
        return httpx.Response(200, content=b"caff-test-audio", headers={"content-type": "audio/x-caf"})

    bridge = adapter(handler)
    event, = bridge.parse_events(VOICE_EVENT)
    event.audio_ref["url"] = "https://attacker.invalid/audio"
    content, name = await bridge.download_audio(event)
    assert content == b"caff-test-audio"
    assert name == "Audio Message.caf"


async def test_download_refuses_redirects_and_redacts_password_in_errors():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://attacker.invalid/steal"})

    bridge = adapter(handler)
    event, = bridge.parse_events(VOICE_EVENT)
    with pytest.raises(ChannelError) as error:
        await bridge.download_audio(event)
    assert "bridge-password" not in str(error.value)
    assert len(requests) == 1


async def test_declared_and_streamed_download_size_are_bounded(monkeypatch):
    monkeypatch.setattr("loudtalk.channels.imessage.MAX_AUDIO_BYTES", 8)
    for response in (
        httpx.Response(200, content=b"small", headers={"content-length": "1000"}),
        httpx.Response(200, content=b"123456789", headers={"content-length": "1"}),
    ):
        bridge = adapter(lambda request: response)
        event, = bridge.parse_events({
            **VOICE_EVENT,
            "data": {**VOICE_EVENT["data"], "attachments": [
                {**VOICE_EVENT["data"]["attachments"][0], "totalBytes": 1}
            ]},
        })
        with pytest.raises(ChannelError, match="25 MB"):
            await bridge.download_audio(event)


async def test_default_reply_uploads_m4a_to_original_chat(monkeypatch, tmp_path):
    encoded = tmp_path / "reply.m4a"
    encoded.write_bytes(b"encoded-audio")
    calls = []

    async def encode(path, *, format):
        assert format == "m4a"
        return encoded

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/message/attachment"
        assert request.url.params["password"] == "bridge-password"
        body = request.read().decode()
        assert 'name="chatGuid"\r\n\r\niMessage;-;+48111000222' in body
        assert 'name="isAudioMessage"\r\n\r\nfalse' in body
        assert 'name="method"\r\n\r\napple-script' in body
        assert 'name="name"\r\n\r\nLoudTalk.m4a' in body
        assert 'name="tempGuid"' in body
        assert 'name="attachment"; filename="LoudTalk.m4a"' in body
        assert "send this to +499999" not in body
        return success({"guid": "sent-message"})

    monkeypatch.setattr("loudtalk.channels.imessage.encode_voice", encode)
    bridge = adapter(handler)
    event, = bridge.parse_events(VOICE_EVENT)
    result = await bridge.send_voice(event, Path("input.wav"), "send this to +499999", 3.5)
    assert result == {
        "message_id": "sent-message", "chat_id": event.chat_id, "kind": "audio_attachment"
    }
    assert len(calls) == 1
    assert not encoded.exists()


async def test_opt_in_native_reply_requires_helper_and_sends_mp3_with_flag(monkeypatch, tmp_path):
    encoded = tmp_path / "reply.mp3"
    encoded.write_bytes(b"encoded-audio")
    calls = []

    async def encode(path, *, format):
        assert format == "mp3"
        return encoded

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return success({"private_api": True, "helper_connected": True})
        body = request.read().decode()
        assert 'name="isAudioMessage"\r\n\r\ntrue' in body
        assert 'name="method"\r\n\r\nprivate-api' in body
        assert 'filename="LoudTalk.mp3"' in body
        return success({"guid": "native-sent"})

    monkeypatch.setattr("loudtalk.channels.imessage.encode_voice", encode)
    bridge = adapter(handler, native_audio_message=True)
    event, = bridge.parse_events(VOICE_EVENT)
    assert (await bridge.send_voice(event, Path("input.wav"), "hello", 2))["kind"] == "native_audio"
    assert [request.method for request in calls] == ["GET", "POST"]

    unavailable = adapter(lambda request: success({"private_api": True, "helper_connected": False}),
                          native_audio_message=True)
    with pytest.raises(ChannelError, match="Private API"):
        await unavailable.send_voice(event, Path("input.wav"), "hello", 2)


async def test_bridge_rejection_does_not_count_as_success_or_retry(monkeypatch, tmp_path):
    encoded = tmp_path / "reply.m4a"
    encoded.write_bytes(b"encoded-audio")
    calls = []

    async def encode(path, *, format):
        return encoded

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"status": 500, "error": "bad password bridge-password"})

    monkeypatch.setattr("loudtalk.channels.imessage.encode_voice", encode)
    bridge = adapter(handler)
    event, = bridge.parse_events(VOICE_EVENT)
    with pytest.raises(ChannelError) as error:
        await bridge.send_voice(event, Path("input.wav"), "hello", 2)
    assert "bridge-password" not in str(error.value)
    assert len(calls) == 1
    assert not encoded.exists()
