"""Mocked Slack wire contracts, request authentication and media host boundaries."""

import hashlib
import hmac
import json
import time

import httpx
import pytest

from loudtalk.channels import slack
from loudtalk.channels.base import ChannelError
from loudtalk.channels.slack import SlackAdapter


@pytest.fixture
def adapter():
    return SlackAdapter({"id": "sl", "secrets": {
        "bot_token": "test-bot-token", "signing_secret": "test-signing-secret",
    }, "settings": {"team_id": "T123", "bot_user_id": "UBOT"}})


def payload(**event_changes):
    event = {"type": "message", "subtype": "file_share", "channel": "C123", "user": "U123",
             "ts": "123.001", "files": [{"id": "F123", "name": "voice.m4a",
                 "mimetype": "audio/mp4", "size": 5,
                 "url_private": "https://files.slack.com/files-pri/T-F/voice.m4a"}]}
    event.update(event_changes)
    return {"type": "event_callback", "team_id": "T123", "event_id": "Ev123", "event": event}


def signed(raw, timestamp=None):
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    signature = "v0=" + hmac.new(b"test-signing-secret", b"v0:" + timestamp.encode() + b":" + raw,
                                hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature}


def mock_http(monkeypatch, handler):
    cls = httpx.AsyncClient
    monkeypatch.setattr(slack.httpx, "AsyncClient", lambda **kw: cls(
        transport=httpx.MockTransport(handler), **kw,
    ))


def test_signed_requests_reject_tamper_and_old_or_future_replay(adapter):
    raw = json.dumps(payload()).encode()
    assert adapter.verify_webhook(raw, signed(raw), {}) is None
    with pytest.raises(ChannelError, match="signature"):
        adapter.verify_webhook(raw + b" ", signed(raw), {})
    for timestamp in (int(time.time()) - 301, int(time.time()) + 301, "bad"):
        with pytest.raises(ChannelError, match="timestamp"):
            adapter.verify_webhook(raw, signed(raw, timestamp), {})
    raw = b'{"type":"url_verification","challenge":"hi"}'
    assert adapter.verify_webhook(raw, signed(raw), {}) == "hi"
    with pytest.raises(ChannelError):
        adapter.verify_webhook(raw, {}, {})


def test_audio_files_and_thread_routing_ignore_bots_and_other_workspaces(adapter):
    event = adapter.parse_events(payload())[0]
    assert event.chat_id == "C123" and event.sender_id == "U123"
    assert event.event_id == "Ev123:F123" and event.thread_id == "123.001"
    assert adapter.parse_events(payload(thread_ts="100.001"))[0].thread_id == "100.001"
    for changes in ({"bot_id": "B123"}, {"user": "UBOT"}, {"subtype": "message_changed"},
                    {"files": [{"id": "F123", "name": "document.pdf", "mimetype": "application/pdf"}]}):
        assert adapter.parse_events(payload(**changes)) == []
    assert adapter.parse_events({**payload(), "team_id": "TOther"}) == []


async def test_connection_reads_bot_identity_without_sending(monkeypatch, adapter):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "user_id": "UBOT", "team_id": "T123"})
    mock_http(monkeypatch, handler)
    assert (await adapter.check_connection())["ok"]
    assert [request.url.path for request in calls] == ["/api/auth.test"]
    assert calls[0].headers["authorization"] == "Bearer test-bot-token"


async def test_private_audio_download_has_bearer_and_bounded_stream(monkeypatch, adapter):
    def handler(request):
        assert request.url.host == "files.slack.com"
        assert request.headers["authorization"] == "Bearer test-bot-token"
        return httpx.Response(200, content=b"audio")
    mock_http(monkeypatch, handler)
    assert await adapter.download_audio(adapter.parse_events(payload())[0]) == (b"audio", "voice.m4a")
    monkeypatch.setattr(slack, "MAX_AUDIO_BYTES", 4)
    event = adapter.parse_events(payload())[0]
    event.audio_ref["size"] = 0
    with pytest.raises(ChannelError, match="exceeds"):
        await adapter.download_audio(event)


@pytest.mark.parametrize("url", ["http://files.slack.com/x", "https://evil.example/x",
    "https://files.slack.com.evil.example/x", "https://files.slack.com:444/x",
    "https://user@files.slack.com/x"])
async def test_refuses_audio_hosts_before_sending_bearer(monkeypatch, adapter, url):
    def handler(request):
        pytest.fail("Unsafe media URL reached the HTTP client")
    mock_http(monkeypatch, handler)
    event = adapter.parse_events(payload())[0]
    event.audio_ref["url"] = url
    with pytest.raises(ChannelError, match="host"):
        await adapter.download_audio(event)


async def test_external_upload_flow_posts_in_same_thread_and_cleans_export(monkeypatch, adapter,
                                                                         tmp_path):
    encoded = tmp_path / "export.mp3"
    encoded.write_bytes(b"MP3 voice")
    async def encode(path, format):
        assert format == "mp3"
        return encoded
    monkeypatch.setattr(slack, "encode_voice", encode)
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("getUploadURLExternal"):
            assert b"filename=reply.mp3" in request.content and b"length=9" in request.content
            return httpx.Response(200, json={"ok": True, "file_id": "FNEW",
                "upload_url": "https://files.slack.com/upload/v1/test"})
        if request.url.host == "files.slack.com":
            assert request.content == b"MP3 voice" and "authorization" not in request.headers
            return httpx.Response(200, content=b"OK - 9")
        assert request.url.path.endswith("completeUploadExternal")
        body = json.loads(request.content)
        assert body["channel_id"] == "C123" and body["thread_ts"] == "100.001"
        assert body["initial_comment"] == "Voice reply text"
        assert body["files"] == [{"id": "FNEW", "title": "Voice reply"}]
        return httpx.Response(200, json={"ok": True, "files": [{"id": "FNEW"}]})
    mock_http(monkeypatch, handler)
    result = await adapter.send_voice(adapter.parse_events(payload(thread_ts="100.001"))[0],
                                     tmp_path / "input.wav", "Voice reply text", 2)
    assert result == {"file_id": "FNEW", "native_voice": False, "kind": "audio_attachment"}
    assert paths == ["/api/files.getUploadURLExternal", "/upload/v1/test",
                     "/api/files.completeUploadExternal"]
    assert not encoded.exists()


async def test_error_does_not_expose_provider_body(monkeypatch, adapter):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={
        "ok": False, "error": "test-bot-token", "needed": "another-private-value",
    }))
    with pytest.raises(ChannelError) as error:
        await adapter.check_connection()
    assert "test-bot-token" not in str(error.value)
    assert "another-private-value" not in str(error.value)


async def test_redirect_never_forwards_bearer_to_other_host(monkeypatch, adapter):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})
    mock_http(monkeypatch, handler)
    with pytest.raises(ChannelError, match="download"):
        await adapter.download_audio(adapter.parse_events(payload())[0])
    assert len(calls) == 1 and calls[0].url.host == "files.slack.com"


async def test_missing_private_url_resolves_through_files_info(monkeypatch, adapter):
    event = adapter.parse_events(payload())[0]
    event.audio_ref["url"] = ""
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/api/files.info":
            assert request.content == b"file=F123"
            return httpx.Response(200, json={"ok": True, "file": {"mimetype": "audio/mp4",
                "url_private": "https://files.slack.com/voice", "size": 5}})
        return httpx.Response(200, content=b"audio")
    mock_http(monkeypatch, handler)
    assert await adapter.download_audio(event) == (b"audio", "voice.m4a")
    assert paths == ["/api/files.info", "/voice"]


async def test_failed_upload_ticket_cleans_export(monkeypatch, adapter, tmp_path):
    encoded = tmp_path / "export.mp3"
    encoded.write_bytes(b"MP3")
    async def encode(path, format):
        return encoded
    monkeypatch.setattr(slack, "encode_voice", encode)
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={
        "ok": True, "file_id": "FNEW", "upload_url": "https://evil.example/upload",
    }))
    with pytest.raises(ChannelError, match="host"):
        await adapter.send_voice(adapter.parse_events(payload())[0], tmp_path / "in.wav", "", 1)
    assert not encoded.exists()
