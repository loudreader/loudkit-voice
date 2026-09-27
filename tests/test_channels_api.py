"""Channel setup and real signed ingress; no live provider or speech model calls."""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from loudtalk.channels import api, registry
from loudtalk.channels.manager import ChannelManager
from loudtalk.channels.slack import SlackAdapter
from loudtalk.channels.whatsapp import WhatsAppAdapter
from loudtalk.store import Store


class NoSpeech:
    def status(self):
        return {"stt": {"state": "not_loaded"}, "tts": {"state": "not_loaded"}}

    def transcribe(self, *args):
        pytest.fail("Setup / webhook acknowledgement must not transcribe audio")

    def synthesize(self, *args):
        pytest.fail("Setup / webhook acknowledgement must not synthesize audio")


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    async def check(self):
        return {"ok": True, "name": "Fixture account"}
    monkeypatch.setattr(SlackAdapter, "check_connection", check)
    monkeypatch.setattr(WhatsAppAdapter, "check_connection", check)
    store = Store(tmp_path)
    store.save_agent({"id": "test-agent", "name": "Test", "kind": "openai", "voice": "gosia",
                      "endpoint": "https://agent.invalid/v1", "model": "test",
                      "api_key": "test-private-agent-key"})
    speech = NoSpeech()
    manager = ChannelManager(tmp_path, store, speech, registry.create_adapter)
    app = FastAPI()
    api.mount_channel_routes(app, manager, speech, store)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, manager, app


def create(client, platform="slack", **overrides):
    secrets = {"slack": {"bot_token": "test-private-bot-token",
                          "signing_secret": "test-private-signing-secret"},
               "whatsapp": {"access_token": "test-private-access", "app_secret": "test-private-app",
                            "verify_token": "test-private-verify"},
               "imessage": {"password": "test-private-bb-password", "webhook_secret": "a" * 32},
               "telegram": {"bot_token": "test-private-tg"}}
    settings = {"slack": {}, "whatsapp": {"phone_number_id": "12345"},
                "imessage": {"server_url": "http://127.0.0.1:1234"}, "telegram": {}}
    body = {"platform": platform, "name": "My channel", "agent_id": "test-agent",
            "secrets": secrets[platform], "settings": settings[platform], **overrides}
    response = client.post("/api/channels", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def slack_event(event_id="Ev123"):
    return {"type": "event_callback", "event_id": event_id, "event": {
        "type": "message", "subtype": "file_share", "channel": "C123", "user": "U123",
        "ts": "123.001", "files": [{"id": "F123", "mimetype": "audio/mp4", "name": "voice.m4a",
            "url_private": "https://files.slack.com/private-token/voice.m4a"}],
    }}


def post_slack(client, channel_id, payload):
    raw = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    signature = "v0=" + hmac.new(b"test-private-signing-secret",
        b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
    return client.post(f"/hooks/{channel_id}", content=raw, headers={
        "X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature,
        "Content-Type": "application/json",
    })


def test_setup_bootstrap_redacts_secrets_and_saves_defaults(mounted):
    client, manager, _ = mounted
    channel = create(client, "whatsapp")
    assert "secrets" not in channel and "access_token" in channel["secret_fields_set"]
    assert manager.get_config(channel["id"])["settings"]["graph_version"] == "v24.0"
    response = client.get("/api/channels/bootstrap")
    assert response.status_code == 200
    body = response.json()
    assert {"channels", "catalog", "agents", "voices", "engine", "native_agents", "pairings",
            "events"} <= body.keys()
    assert "test-private" not in response.text
    assert {row["id"] for row in body["catalog"]} == {
        "slack", "whatsapp", "telegram", "discord", "imessage",
    }


def test_edit_preserves_blank_secrets_enable_disable_and_missing_id(mounted):
    client, manager, _ = mounted
    channel = create(client)
    path = f"/api/channels/{channel['id']}"
    changed = client.patch(path, json={"name": "  Renamed  ", "secrets": {"bot_token": ""}})
    assert changed.status_code == 200 and changed.json()["name"] == "Renamed"
    assert manager.get_config(channel["id"])["secrets"]["bot_token"] == "test-private-bot-token"
    assert client.post(path + "/check").json()["ok"]
    assert client.post(path + "/enable").json()["enabled"]
    assert client.patch(path, json={"name": "Cannot edit active"}).status_code == 400
    assert not client.post(path + "/disable").json()["enabled"]
    assert client.post("/api/channels/missing/check").status_code == 404


@pytest.mark.parametrize("updates", [
    {"platform": "slack", "name": "x", "agent_id": "test-agent", "secrets": {"bot_token": 123}},
    {"platform": "slack", "name": "x", "agent_id": "test-agent", "enabled": "true"},
    {"platform": "slack", "name": "x", "agent_id": "test-agent", "unexpected": "secret-value"},
    {"platform": "slack", "name": " ", "agent_id": "test-agent"},
    {"platform": "slack", "name": "x", "agent_id": "test-agent",
     "secrets": {"bot_token": "private-value" * 1000}},
])
def test_invalid_fields_do_not_echo_submitted_secrets(mounted, updates):
    client, _, _ = mounted
    result = client.post("/api/channels", json=updates)
    assert result.status_code == 422
    assert "secret-value" not in result.text and "private-value" not in result.text
    assert "input" not in result.text


def test_unknown_settings_and_patch_null_rejected(mounted):
    client, _, _ = mounted
    channel = create(client)
    path = f"/api/channels/{channel['id']}"
    assert client.patch(path, json={"name": None}).status_code == 422
    assert client.patch(path, json={"settings": {"malicious_url": "https://evil.example"}}).status_code == 400
    assert client.patch(path, json={"platform": "telegram"}).status_code == 422


def test_native_setup_is_generated_without_account_access(mounted):
    client, _, _ = mounted
    assert {item["id"] for item in client.get("/api/native-agents").json()} == {"hermes", "openclaw"}
    result = client.post("/api/native-agents/hermes/setup", json={"voice": "gosia"})
    assert result.status_code == 200
    assert result.json()["connection_status"] == "not_checked"
    assert result.json()["config"]["stt"]["openai"]["base_url"] == "http://127.0.0.1:8765/v1"
    assert client.post("/api/native-agents/hermes/setup", json={"base_url": "https://evil.example/v1"}).status_code == 400


def test_signed_webhook_pairs_without_audio_and_queue_deduplicates(mounted):
    client, manager, _ = mounted
    channel = create(client)
    channel_id = channel["id"]
    assert client.post(f"/api/channels/{channel_id}/enable").status_code == 200
    assert post_slack(client, channel_id, slack_event()).status_code == 200
    pairing = manager.pairings()[0]
    assert pairing["chat_id"] == "C123" and pairing["sender_id"] == "U123"
    assert manager.events() == []
    assert client.post(f"/api/channels/{channel_id}/pairings/{pairing['id']}/approve").status_code == 200
    assert post_slack(client, channel_id, slack_event("EvNew")).status_code == 200
    assert post_slack(client, channel_id, slack_event("EvNew")).status_code == 200
    assert len(manager.events()) == 1 and manager.events()[0]["status"] == "queued"
    assert "private-token" not in client.get("/api/channels/bootstrap").text


def test_slack_challenge_works_disabled_but_audio_events_are_ignored(mounted):
    client, manager, _ = mounted
    channel_id = create(client)["id"]
    response = post_slack(client, channel_id, {"type": "url_verification", "challenge": "test-challenge"})
    assert response.status_code == 200 and response.json() == {"challenge": "test-challenge"}
    assert post_slack(client, channel_id, slack_event()).json()["ignored"] == "disabled"
    assert manager.pairings() == []
    assert client.get(f"/hooks/{channel_id}").status_code == 405


def test_whatsapp_challenge_is_plain_text_while_disabled(mounted):
    client, _, _ = mounted
    channel_id = create(client, "whatsapp")["id"]
    path = f"/hooks/{channel_id}"
    query = {"hub.mode": "subscribe", "hub.verify_token": "test-private-verify", "hub.challenge": "987"}
    response = client.get(path, params=query)
    assert response.status_code == 200 and response.text == "987"
    assert response.headers["content-type"].startswith("text/plain")
    query["hub.verify_token"] = "wrong"
    assert client.get(path, params=query).status_code == 403


def test_bad_signature_rejected_before_json_parsing(mounted):
    client, _, _ = mounted
    channel_id = create(client, "whatsapp")["id"]
    response = client.post(f"/hooks/{channel_id}", content=b"not json")
    assert response.status_code == 403 and "test-private" not in response.text
    assert client.post("/hooks/missing", content=b"{}").status_code == 404
    channel_id = create(client, "telegram")["id"]
    assert client.post(f"/hooks/{channel_id}", content=b"{}").status_code == 404


def test_webhook_content_length_limit_before_auth(mounted):
    client, _, _ = mounted
    channel_id = create(client)["id"]
    response = client.post(f"/hooks/{channel_id}", content=b"{}",
                           headers={"Content-Length": str(api.MAX_WEBHOOK_BYTES + 1)})
    assert response.status_code == 413


async def test_webhook_stream_limit_without_content_length(mounted):
    client, _, app = mounted
    channel_id = create(client)["id"]
    async def body():
        yield b"x" * api.MAX_WEBHOOK_BYTES
        yield b"x"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as request:
        response = await request.post(f"/hooks/{channel_id}", content=body())
    assert response.status_code == 413


def test_queue_refusal_is_retryable_without_false_ack(mounted, monkeypatch):
    client, manager, _ = mounted
    channel_id = create(client)["id"]
    client.post(f"/api/channels/{channel_id}/enable")
    async def refuse(event):
        return False
    monkeypatch.setattr(manager, "accept", refuse)
    assert post_slack(client, channel_id, slack_event()).status_code == 503


def test_webhook_info_only_explicit_bb_token_response(mounted):
    client, _, _ = mounted
    channel_id = create(client, "imessage")["id"]
    response = client.post(f"/api/channels/{channel_id}/webhook-info", json={
        "public_base_url": "http://192.168.1.3:8765",
    })
    assert response.status_code == 200
    assert parse_qs(urlsplit(response.json()["url"]).query) == {"token": ["a" * 32]}
    assert "test-private-bb-password" not in response.text
    assert "a" * 32 not in client.get("/api/channels/bootstrap").text
    channel_id = create(client)["id"]
    response = client.post(f"/api/channels/{channel_id}/webhook-info", json={
        "public_base_url": "https://voice.example/prefix/",
    })
    assert response.json()["url"] == f"https://voice.example/prefix/hooks/{channel_id}"


@pytest.mark.parametrize("value", ["http://example.com", "https://u:p@example.com",
    "https://example.com?token=private", "https://example.com#fragment", "file:///tmp/test",
    "https://example.com:0", "https://example.com/../private", "https://@example.com"])
def test_webhook_info_rejects_bad_or_unencrypted_urls(mounted, value):
    client, _, _ = mounted
    channel_id = create(client)["id"]
    assert client.post(f"/api/channels/{channel_id}/webhook-info", json={
        "public_base_url": value,
    }).status_code == 400
