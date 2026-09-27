"""Executable transport proof; no Grok Bot account or real messenger is contacted.

Run from the repository root:
  PYTHONPATH=src .venv/bin/python -m pytest integrations/grokbot/test_transport.py -q

Speech and messenger I/O are explicit doubles. The server, SQLite routing,
actual CLI subprocess, loopback HTTP and MCP SDK are production components.
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from mcp.server.mcpserver.exceptions import ToolError

from loudtalk.channels.base import ChannelError, Incoming
from loudtalk.mcp import build_server
from loudtalk.server import create_app
from loudtalk.speech import SpeechService


def tone():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 4000)
    return buffer.getvalue()


class FixtureSpeech(SpeechService):
    def __init__(self, data_dir):
        super().__init__(data_dir)
        self.transcriptions = 0
        self.syntheses = 0

    def _save(self):
        audio_id = uuid4().hex
        (self.audio_dir / f"{audio_id}.wav").write_bytes(tone())
        return {
            "audio_id": audio_id,
            "audio_url": f"/audio/{audio_id}.wav",
            "duration": 0.25,
        }

    def transcribe(self, path):
        with wave.open(str(path), "rb") as wav:
            assert wav.getnframes() == 4000
        self.transcriptions += 1
        return {"text": "Sprawdź moje zadanie.", **self._save()}

    def synthesize(self, text, voice):
        assert text.strip()
        self.syntheses += 1
        return self._save()


class FixtureMessenger:
    def __init__(self, config):
        self.config = config
        self.downloads = 0
        self.deliveries = []
        self.fail_send = False

    async def check_connection(self):
        return {"ok": True, "identity": "Explicit test messenger"}

    def verify_webhook(self, raw, headers, query):
        if headers.get("x-fixture-auth") != "fixture-only":
            raise ChannelError("Nieprawidłowy podpis testowy.")
        return None

    def parse_events(self, payload):
        return [
            Incoming(
                channel_id=self.config["id"],
                event_id=payload["id"],
                chat_id=payload["chat"],
                sender_id=payload["sender"],
                thread_id=payload["thread"],
                audio_ref={"fixture": True},
                filename="fixture.wav",
            )
        ]

    async def download_audio(self, event):
        self.downloads += 1
        return tone(), "fixture.wav"

    async def send_voice(self, event, path, text, duration):
        with wave.open(str(path), "rb") as wav:
            assert wav.getnframes() == 4000
        self.deliveries.append(
            (event.event_id, event.chat_id, event.thread_id, event.sender_id, text)
        )
        if self.fail_send:
            raise TimeoutError("Fixture: receipt unavailable after sending")
        return {"ok": True}


@pytest.fixture
def local_service(tmp_path):
    speech = FixtureSpeech(tmp_path)
    adapters = {}

    def factory(config):
        adapter = FixtureMessenger(config)
        adapters[config["id"]] = adapter
        return adapter

    app = create_app(tmp_path, speech, adapter_factory=factory)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    service = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    worker = threading.Thread(target=service.run, kwargs={"sockets": [listener]}, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while not service.started and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        assert service.started, "Local test HTTP service failed to start"
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
            yield client, app, speech, adapters
    finally:
        service.should_exit = True
        worker.join(timeout=10)
        listener.close()


def cli(client, *args):
    project = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "PYTHONPATH": str(project / "src")}
    return subprocess.run(
        [sys.executable, "-m", "loudtalk.cli", "--url", str(client.base_url), *args],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def await_status(client, event_id, status):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        events = client.get("/api/channels/bootstrap").json()["events"]
        matches = [item for item in events if item["event_id"] == event_id]
        if matches and matches[0]["status"] == status:
            return matches[0]
        time.sleep(0.01)
    pytest.fail(f"Event {event_id} did not reach {status}: {events}")


@pytest.mark.parametrize("uncertain", [False, True])
def test_existing_agent_cli_reply_returns_to_original_messenger(local_service, uncertain):
    client, app, speech, adapters = local_service
    created = client.post(
        "/api/agents", json={"name": "Existing Grok Bot fixture", "kind": "inbox"}
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()["id"]
    saved = client.post(
        "/api/channels",
        json={
            "platform": "slack",
            "name": "Fixture chat",
            "agent_id": agent_id,
            "secrets": {"bot_token": "xoxb-fixture-only", "signing_secret": "fixture-only"},
        },
    )
    assert saved.status_code == 200, saved.text
    channel_id = saved.json()["id"]
    enabled = client.post(f"/api/channels/{channel_id}/enable")
    assert enabled.status_code == 200, enabled.text
    adapter = adapters[channel_id]
    adapter.fail_send = uncertain
    hook = f"/hooks/{channel_id}"
    event = {"id": "unpaired", "chat": "my-chat", "sender": "my-user", "thread": "my-thread"}
    received = client.post(hook, json=event, headers={"x-fixture-auth": "fixture-only"})
    assert received.status_code in {200, 202}, received.text
    pairing = client.get("/api/channels/bootstrap").json()["pairings"][0]
    assert speech.transcriptions == adapter.downloads == 0
    approved = client.post(f"/api/channels/{channel_id}/pairings/{pairing['id']}/approve")
    assert approved.status_code == 200, approved.text
    event["id"] = "paired-voice"
    for _ in range(2):
        received = client.post(hook, json=event, headers={"x-fixture-auth": "fixture-only"})
        assert received.status_code in {200, 202}, received.text
    pending = await_status(client, "paired-voice", "awaiting_agent")
    assert not adapter.deliveries  # No model is silently substituted for the real Bot.

    inbox = cli(client, "inbox", agent_id, "--after", "0")
    assert inbox.returncode == 0, inbox.stderr
    messages = json.loads(inbox.stdout)
    assert len(messages) == 1
    assert messages[0]["text"] == "Sprawdź moje zadanie."
    assert messages[0]["conversation_id"] == pending["conversation_id"]
    assert speech.transcriptions == adapter.downloads == 1

    # Emulates the text supplied by the user's existing Bot, not an API model.
    reply = cli(
        client,
        "send",
        agent_id,
        "Odpowiedź istniejącego Bota.",
        "--conversation",
        messages[0]["conversation_id"],
        "--reply-to",
        str(messages[0]["id"]),
    )
    result = json.loads(reply.stdout)
    assert reply.returncode == int(uncertain), reply.stderr
    expected = "uncertain" if uncertain else "sent"
    assert result["delivery"]["status"] == expected
    assert await_status(client, "paired-voice", expected)["reply"] == "Odpowiedź istniejącego Bota."
    assert adapter.deliveries == [
        ("paired-voice", "my-chat", "my-thread", "my-user", "Odpowiedź istniejącego Bota.")
    ]
    after = cli(client, "inbox", agent_id, "--after", str(messages[0]["id"]))
    assert after.returncode == 0 and json.loads(after.stdout) == []
    if uncertain:
        assert "przed ponowieniem" in reply.stderr


@pytest.mark.parametrize("uncertain", [False, True])
def test_explicit_voice_id_routes_out_of_order_and_deduplicates_reply(local_service, uncertain):
    client, _, speech, adapters = local_service
    created = client.post("/api/agents", json={"name": "Existing Bot", "kind": "inbox"})
    assert created.status_code == 200, created.text
    agent_id = created.json()["id"]
    saved = client.post(
        "/api/channels",
        json={
            "platform": "slack",
            "name": "Fixture chat",
            "agent_id": agent_id,
            "secrets": {"bot_token": "xoxb-fixture-only", "signing_secret": "fixture-only"},
            "allowed_chats": ["same-chat"],
            "allowed_senders": ["same-user"],
        },
    )
    assert saved.status_code == 200, saved.text
    channel_id = saved.json()["id"]
    enabled = client.post(f"/api/channels/{channel_id}/enable")
    assert enabled.status_code == 200, enabled.text
    adapter = adapters[channel_id]
    adapter.fail_send = uncertain
    pending = []
    for event_id in ("first-voice", "second-voice"):
        received = client.post(
            f"/hooks/{channel_id}",
            json={"id": event_id, "chat": "same-chat", "sender": "same-user", "thread": "thread"},
            headers={"x-fixture-auth": "fixture-only"},
        )
        assert received.status_code in {200, 202}, received.text
        pending.append(await_status(client, event_id, "awaiting_agent"))
    assert pending[0]["conversation_id"] == pending[1]["conversation_id"]
    inbox = cli(client, "inbox", agent_id)
    assert inbox.returncode == 0, inbox.stderr
    messages = json.loads(inbox.stdout)
    assert len(messages) == 2
    conversation_id = messages[0]["conversation_id"]

    ambiguous = client.post(
        "/api/agent-messages",
        json={"agent_id": agent_id, "text": "No incoming ID", "conversation_id": conversation_id},
    )
    assert ambiguous.status_code in {400, 422}, ambiguous.text
    assert speech.syntheses == 0 and adapter.deliveries == []

    # Answer the second voice first: FIFO alone would associate this incorrectly.
    second = cli(
        client,
        "send",
        agent_id,
        "Odpowiedź na drugą głosówkę.",
        "--conversation",
        conversation_id,
        "--reply-to",
        str(messages[1]["id"]),
    )
    assert second.returncode == int(uncertain), second.stderr
    second_result = json.loads(second.stdout)
    expected = "uncertain" if uncertain else "sent"
    await_status(client, "second-voice", expected)
    await_status(client, "first-voice", "awaiting_agent")
    assert adapter.deliveries == [
        ("second-voice", "same-chat", "thread", "same-user", "Odpowiedź na drugą głosówkę.")
    ]

    retried = client.post(
        "/api/agent-messages",
        json={
            "agent_id": agent_id,
            "text": "Odpowiedź na drugą głosówkę.",
            "conversation_id": conversation_id,
            "reply_to_message_id": messages[1]["id"],
        },
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["id"] == second_result["id"]
    assert retried.json()["delivery"] == second_result["delivery"]
    assert speech.syntheses == 1 and len(adapter.deliveries) == 1
    await_status(client, "first-voice", "awaiting_agent")

    adapter.fail_send = False
    first = cli(
        client,
        "send",
        agent_id,
        "Odpowiedź na pierwszą głosówkę.",
        "--conversation",
        conversation_id,
        "--reply-to",
        str(messages[0]["id"]),
    )
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["id"] != second_result["id"]
    await_status(client, "first-voice", "sent")
    assert speech.syntheses == 2 and len(adapter.deliveries) == 2
    assert adapter.deliveries[1][0] == "first-voice"


@pytest.mark.parametrize("status", ["sent", "uncertain", "error", "processing"])
async def test_mcp_reports_only_confirmed_messenger_delivery(status):
    calls = []

    def service(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "id": 42,
                "status": "ready",
                "audio_url": "/audio/reply.wav",
                "delivery": {"event_id": "event-fixture", "status": status},
            },
        )

    mcp = build_server(_transport=httpx.MockTransport(service))
    tool = next(item for item in await mcp.list_tools() if item.name == "send_voice_message")
    assert tool.annotations.open_world_hint is True
    args = {
        "agent_id": "grok-bot",
        "text": "Gotowe.",
        "conversation_id": "same-chat",
        "reply_to_message_id": 123,
    }
    if status == "sent":
        result = await mcp.call_tool("send_voice_message", args)
        assert result.structured_content["delivery"]["status"] == "sent"
    else:
        with pytest.raises(ToolError, match="before resending"):
            await mcp.call_tool("send_voice_message", args)
    assert len(calls) == 1
    assert json.loads(calls[0].content)["reply_to_message_id"] == 123
