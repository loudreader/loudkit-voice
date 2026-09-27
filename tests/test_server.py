"""API integration tests with real WAV containers and an explicit speech double.

These tests exercise persistence, HTTP contracts, concurrency and failures. They
do not load models or claim to validate transcription/synthesis quality; the
opt-in smoke_speech.py covers the real speech engines.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import struct
import threading
import time
import wave
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from loudtalk import dispatch, server
from loudtalk.speech import SpeechService


def wav_bytes() -> bytes:
    """A 250 ms tone, deliberately not a speech recording."""
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(
            b"".join(
                struct.pack("<h", round(1000 * math.sin(2 * math.pi * 440 * n / 16000)))
                for n in range(4000)
            )
        )
    return output.getvalue()


class FakeSpeech(SpeechService):
    """Fixed transcript and tone output; keep production audio-path validation."""

    def __init__(self, data_dir: Path):
        super().__init__(data_dir)
        self.synthesis_calls: list[tuple[str, str]] = []
        self.failures_left = 0
        self.calls_lock = threading.Lock()

    def _save(self, data: bytes) -> dict:
        audio_id = uuid4().hex
        destination = self.audio_dir / f"{audio_id}.wav"
        destination.write_bytes(data)
        with wave.open(str(destination), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
        return {"audio_id": audio_id, "audio_url": f"/audio/{audio_id}.wav", "duration": duration}

    def synthesize(self, text: str, voice: str) -> dict:
        with self.calls_lock:
            self.synthesis_calls.append((text, voice))
            if self.failures_left:
                self.failures_left -= 1
                raise RuntimeError("Test: synteza chwilowo niedostępna.")
        return self._save(wav_bytes())

    def transcribe(self, path: Path) -> dict:
        content = path.read_bytes()
        try:
            with wave.open(io.BytesIO(content), "rb") as audio:
                assert audio.getnframes() > 0
        except (wave.Error, EOFError, AssertionError) as exc:
            raise ValueError("Nie można odczytać audio. Użyj poprawnego pliku WAV.") from exc
        return {"text": "Stała transkrypcja testowa.", **self._save(content)}


@pytest.fixture
def running(tmp_path):
    speech = FakeSpeech(tmp_path)
    app = server.create_app(tmp_path, speech)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, app, speech


def create_agent(client, **overrides):
    response = client.post("/api/agents", json={"name": "Test Agent", **overrides})
    assert response.status_code == 200, response.text
    return response.json()


def create_conversation(client, agent_id="hermes"):
    response = client.post("/api/conversations", json={"agent_id": agent_id})
    assert response.status_code == 200, response.text
    return response.json()


def wait_for_reply(client, conversation_id, expected="ready"):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
        replies = [message for message in messages if message["role"] == "assistant"]
        if replies and replies[-1]["status"] == expected:
            return replies[-1]
        time.sleep(0.01)
    pytest.fail(f"Reply did not reach {expected!r}: {messages!r}")


def assert_wav(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("audio/wav")
    with wave.open(io.BytesIO(response.content), "rb") as audio:
        assert audio.getnframes() == 4000
        assert audio.getframerate() == 16000


def test_record_edit_send_and_receive_agent_voice(running):
    client, _, speech = running
    conversation = create_conversation(client)
    messages_url = f"/api/conversations/{conversation['id']}/messages"

    uploaded = client.post(
        "/api/transcribe", files={"file": ("recording.wav", wav_bytes(), "audio/wav")}
    )
    assert uploaded.status_code == 200, uploaded.text
    draft = uploaded.json()
    assert draft["text"] == "Stała transkrypcja testowa."
    assert client.get(messages_url).json() == []  # Editing happens before delivery.

    sent = client.post(
        messages_url, json={"text": "  Poprawiona wiadomość.  ", "audio_id": draft["audio_id"]}
    )
    assert sent.status_code == 200, sent.text
    message = sent.json()
    assert message["text"] == "Poprawiona wiadomość."
    assert message["role"] == "user"
    assert message["duration"] == pytest.approx(0.25)
    assert_wav(client.get(message["audio_url"]))
    assert client.get("/api/agents/hermes/inbox").json() == [message]
    assert client.get(f"/api/agents/hermes/inbox?after_id={message['id']}").json() == []

    response = client.post(
        "/api/agent-messages",
        json={
            "agent_id": "hermes",
            "conversation_id": conversation["id"],
            "text": "Odpowiedź agenta.",
        },
    )
    assert response.status_code == 200, response.text
    reply = response.json()
    assert reply["status"] == "ready"
    assert reply["text"] == "Odpowiedź agenta."
    assert reply["role"] == "assistant"
    assert_wav(client.get(reply["audio_url"]))
    assert speech.synthesis_calls == [("Odpowiedź agenta.", "gosia")]
    assert client.get("/api/agents/hermes/inbox").json() == [message]
    assert [m["role"] for m in client.get(messages_url).json()] == ["user", "assistant"]
    assert (
        client.get("/api/bootstrap").json()["conversations"][0]["title"] == "Poprawiona wiadomość."
    )


def test_agent_can_start_a_conversation(running):
    client, _, _ = running
    response = client.post(
        "/api/agent-messages", json={"agent_id": "hermes", "text": "Gotowy raport."}
    )
    assert response.status_code == 200, response.text
    reply = response.json()
    conversations = client.get("/api/bootstrap").json()["conversations"]
    assert len(conversations) == 1
    assert conversations[0]["id"] == reply["conversation_id"]
    assert conversations[0]["agent_id"] == "hermes"
    assert_wav(client.get(reply["audio_url"]))


def test_conversation_cannot_receive_another_agents_reply(running):
    client, _, _ = running
    other = create_agent(client, name="Other")
    conversation = create_conversation(client)
    response = client.post(
        "/api/agent-messages",
        json={
            "agent_id": other["id"],
            "conversation_id": conversation["id"],
            "text": "Wrong agent",
        },
    )
    assert response.status_code == 400, response.text
    assert "innego agenta" in response.json()["detail"]
    assert client.get(f"/api/conversations/{conversation['id']}/messages").json() == []


def test_secrets_stay_out_of_public_responses_and_survive_partial_update(running):
    client, app, _ = running
    secret = "test-secret-should-never-be-in-bootstrap"
    agent = create_agent(
        client,
        kind="openai",
        endpoint="http://localhost:9876/v1",
        model="test-model",
        api_key=secret,
    )
    assert agent["has_api_key"] is True
    assert "api_key" not in agent
    assert secret not in client.get("/api/bootstrap").text
    updated = client.patch(f"/api/agents/{agent['id']}", json={"name": "Renamed"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["has_api_key"] is True
    assert secret not in updated.text
    assert app.state.store.agent(agent["id"])["api_key"] == secret
    cleared = client.patch(f"/api/agents/{agent['id']}", json={"api_key": ""})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["has_api_key"] is False


def test_conversations_messages_audio_and_agent_configuration_survive_restart(tmp_path):
    with TestClient(server.create_app(tmp_path, FakeSpeech(tmp_path))) as client:
        agent = create_agent(client, name="Durable Agent", api_key="durable-test-key")
        conversation = create_conversation(client, agent["id"])
        sent = client.post(
            f"/api/conversations/{conversation['id']}/messages", json={"text": "Remember me"}
        ).json()
        reply = client.post(
            "/api/agent-messages",
            json={
                "agent_id": agent["id"],
                "conversation_id": conversation["id"],
                "text": "I remember",
            },
        ).json()
    with TestClient(server.create_app(tmp_path, FakeSpeech(tmp_path))) as restarted:
        bootstrap = restarted.get("/api/bootstrap").json()
        saved = next(a for a in bootstrap["agents"] if a["id"] == agent["id"])
        assert saved["name"] == "Durable Agent" and saved["has_api_key"]
        assert restarted.get(f"/api/conversations/{conversation['id']}/messages").json() == [
            sent,
            reply,
        ]
        assert_wav(restarted.get(reply["audio_url"]))
        assert restarted.delete(f"/api/conversations/{conversation['id']}").status_code == 200
        assert restarted.get(f"/api/conversations/{conversation['id']}/messages").status_code == 404
        assert restarted.get(f"/api/agents/{agent['id']}/inbox").json() == []


def test_retry_tts_failure_reuses_generated_text(running, monkeypatch):
    client, _, speech = running
    calls = []

    async def generate(agent, history, conversation_id):
        calls.append((agent, history, conversation_id))
        return "Generated only once."

    monkeypatch.setattr(server, "generate_reply", generate)
    speech.failures_left = 1
    agent = create_agent(client, kind="openai", endpoint="http://localhost:9876/v1", model="test")
    conversation = create_conversation(client, agent["id"])
    sent = client.post(f"/api/conversations/{conversation['id']}/messages", json={"text": "Hello"})
    assert sent.status_code == 200
    failed = wait_for_reply(client, conversation["id"], "error")
    assert failed["text"] == "Generated only once."
    assert "synteza" in failed["error"]
    assert client.post(f"/api/messages/{failed['id']}/retry").status_code == 200
    reply = wait_for_reply(client, conversation["id"])
    assert reply["id"] == failed["id"]
    assert reply["error"] is None
    assert len(calls) == 1
    assert [m["content"] for m in calls[0][1]] == ["Hello"]
    assert speech.synthesis_calls == [("Generated only once.", "gosia")] * 2
    assert_wav(client.get(reply["audio_url"]))
    assert client.post(f"/api/messages/{reply['id']}/retry").status_code == 409


def test_interrupted_reply_is_recoverable_after_restart(tmp_path, monkeypatch):
    original = server.create_app(tmp_path, FakeSpeech(tmp_path))
    conversation = original.state.store.create_conversation("hermes")
    pending = original.state.store.add_message(
        conversation["id"], "assistant", "Saved reply", status="pending"
    )

    async def forbidden_generate(*args):
        pytest.fail("Recovery must reuse the already generated reply")

    monkeypatch.setattr(server, "generate_reply", forbidden_generate)
    with TestClient(server.create_app(tmp_path, FakeSpeech(tmp_path))) as restarted:
        messages = restarted.get(f"/api/conversations/{conversation['id']}/messages").json()
        assert messages[0]["status"] == "error"
        assert messages[0]["text"] == "Saved reply"
        assert restarted.post(f"/api/messages/{pending['id']}/retry").status_code == 200
        reply = wait_for_reply(restarted, conversation["id"])
        assert_wav(restarted.get(reply["audio_url"]))


@pytest.mark.asyncio
async def test_concurrent_sends_are_rejected_while_one_reply_is_pending(tmp_path, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    generation_calls = []

    async def generate(*args):
        generation_calls.append(args)
        started.set()
        await release.wait()
        return "One response"

    monkeypatch.setattr(server, "generate_reply", generate)
    app = server.create_app(tmp_path, FakeSpeech(tmp_path))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            agent = (
                await client.post(
                    "/api/agents",
                    json={
                        "name": "Automatic",
                        "kind": "openai",
                        "endpoint": "http://localhost:9876/v1",
                        "model": "test",
                    },
                )
            ).json()
            conversation = (
                await client.post("/api/conversations", json={"agent_id": agent["id"]})
            ).json()
            endpoint = f"/api/conversations/{conversation['id']}/messages"
            try:
                responses = await asyncio.gather(
                    *[client.post(endpoint, json={"text": "Double tap"}) for _ in range(2)]
                )
                await asyncio.wait_for(started.wait(), timeout=2)
                assert sorted(r.status_code for r in responses) == [200, 409]
                messages = (await client.get(endpoint)).json()
                assert [m["role"] for m in messages] == ["user", "assistant"]
                assert messages[-1]["status"] == "pending"
                assert (
                    await client.delete(f"/api/conversations/{conversation['id']}")
                ).status_code == 409
            finally:
                release.set()
                await asyncio.gather(*list(app.state.tasks))
            assert len(generation_calls) == 1
            messages = (await client.get(endpoint)).json()
            assert messages[-1]["status"] == "ready"


def test_openai_speech_returns_downloadable_wav(running):
    client, _, speech = running
    response = client.post(
        "/v1/audio/speech",
        json={"input": "Read this", "voice": "joe", "model": "loudkit", "response_format": "wav"},
    )
    assert_wav(response)
    assert "speech.wav" in response.headers["content-disposition"]
    assert speech.synthesis_calls == [("Read this", "joe")]
    assert (
        client.post(
            "/v1/audio/speech", json={"input": "Hello", "response_format": "ogg"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("response_format", ["json", "text", "verbose_json"])
def test_openai_transcription_formats(running, response_format):
    client, _, _ = running
    response = client.post(
        "/v1/audio/transcriptions",
        data={"model": "parakeet", "response_format": response_format},
        files={"file": ("recording.wav", wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 200, response.text
    if response_format == "text":
        assert response.text == "Stała transkrypcja testowa."
        assert response.headers["content-type"].startswith("text/plain")
    else:
        expected = {"text": "Stała transkrypcja testowa."}
        if response_format == "verbose_json":
            expected["duration"] = 0.25
        assert response.json() == expected


@pytest.mark.parametrize("endpoint", ["/api/transcribe", "/v1/audio/transcriptions"])
@pytest.mark.parametrize("content, expected_status", [(None, 422), (b"", 400), (b"not audio", 400)])
def test_missing_empty_and_invalid_uploads_are_useful_errors(
    running, endpoint, content, expected_status
):
    client, app, _ = running
    files = None if content is None else {"file": ("bad.wav", content, "audio/wav")}
    response = client.post(endpoint, files=files)
    assert response.status_code == expected_status, response.text
    assert response.json()["detail"]
    incoming = app.state.store.path.parent / "incoming"
    assert not incoming.exists() or list(incoming.iterdir()) == []


def test_invalid_transcription_format_and_oversized_requests_are_rejected(running):
    client, _, _ = running
    response = client.post(
        "/v1/audio/transcriptions",
        data={"response_format": "srt"},
        files={"file": ("recording.wav", wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 400
    assert "formaty" in response.json()["detail"]
    assert (
        client.post(
            "/api/transcribe", content=b"x", headers={"content-length": str(27 * 1024 * 1024)}
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/api/transcribe", content=b"x", headers={"content-length": "invalid"}
        ).status_code
        == 400
    )


def test_invalid_and_missing_audio_ids_return_actionable_errors(running):
    client, _, _ = running
    missing_id = "0" * 32
    for path, expected_status in (("/audio/invalid.wav", 400), (f"/audio/{missing_id}.wav", 404)):
        response = client.get(path)
        assert response.status_code == expected_status, response.text
        assert response.json()["detail"]
    conversation = create_conversation(client)
    endpoint = f"/api/conversations/{conversation['id']}/messages"
    for audio_id, status in (("../../private", 400), (missing_id, 404)):
        response = client.post(endpoint, json={"text": "Message", "audio_id": audio_id})
        assert response.status_code == status, response.text
    assert client.get(endpoint).json() == []


@pytest.mark.parametrize(
    "headers, expected_status",
    [
        ({"origin": "http://evil.example"}, 403),
        ({"origin": "null"}, 403),
        ({"origin": "http://testserver", "sec-fetch-site": "cross-site"}, 403),
        ({"host": "evil.example"}, 400),
        ({"origin": "http://testserver"}, 200),
        ({}, 200),
    ],
)
def test_local_host_and_same_origin_protection(running, headers, expected_status):
    client, _, _ = running
    response = client.get("/api/bootstrap", headers=headers)
    assert response.status_code == expected_status, response.text
    if expected_status == 200:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_cross_origin_mutation_creates_nothing(running):
    client, _, _ = running
    before = client.get("/api/bootstrap").json()["agents"]
    response = client.post(
        "/api/agents", json={"name": "Untrusted"}, headers={"origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert client.get("/api/bootstrap").json()["agents"] == before


@pytest.mark.parametrize("kind", ["openai", "webhook"])
def test_automatic_reply_uses_real_dispatch_with_transcript_and_history(running, monkeypatch, kind):
    """Stub only provider HTTP; exercise the real server/adapter data contract."""
    client, _, speech = running
    received = []
    expected_replies = ["Pierwsza odpowiedź.", "Odpowiedź z pamięcią rozmowy."]

    def provider(request):
        payload = json.loads(request.content)
        received.append((request, payload))
        reply = expected_replies[len(received) - 1]
        if kind == "openai":
            return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
        return httpx.Response(200, json={"text": reply})

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(provider)

    def provider_client(**kwargs):
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(dispatch.httpx, "AsyncClient", provider_client)
    agent = create_agent(
        client,
        name="Connected agent",
        kind=kind,
        endpoint="http://provider.test/v1" if kind == "openai" else "http://provider.test/reply",
        model="test-model",
        api_key="provider-test-key",
    )
    conversation = create_conversation(client, agent["id"])
    endpoint = f"/api/conversations/{conversation['id']}/messages"
    upload = client.post(
        "/api/transcribe", files={"file": ("recording.wav", wav_bytes(), "audio/wav")}
    )
    assert upload.status_code == 200
    assert upload.json()["text"] == "Stała transkrypcja testowa."
    edited_transcript = "Poprawiona transkrypcja wysłana do agenta."
    sent = client.post(
        endpoint, json={"text": edited_transcript, "audio_id": upload.json()["audio_id"]}
    )
    assert sent.status_code == 200
    first_reply = wait_for_reply(client, conversation["id"])
    assert first_reply["text"] == expected_replies[0]
    assert_wav(client.get(first_reply["audio_url"]))

    assert (
        client.post(endpoint, json={"text": "Pamiętasz poprzednią wiadomość?"}).status_code == 200
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        messages = client.get(endpoint).json()
        if len(messages) == 4 and messages[-1]["status"] == "ready":
            break
        time.sleep(0.01)
    else:
        pytest.fail(f"Second automatic reply failed: {messages!r}")
    assert messages[-1]["text"] == expected_replies[1]
    assert_wav(client.get(messages[-1]["audio_url"]))
    assert len(received) == 2
    first_request, first_payload = received[0]
    second_request, second_payload = received[1]
    assert first_request.headers["authorization"] == "Bearer provider-test-key"
    assert second_request.headers["authorization"] == "Bearer provider-test-key"
    expected_first = [{"role": "user", "content": edited_transcript}]
    expected_history = [
        *expected_first,
        {"role": "assistant", "content": expected_replies[0]},
        {"role": "user", "content": "Pamiętasz poprzednią wiadomość?"},
    ]
    if kind == "openai":
        assert first_request.url.path == "/v1/chat/completions"
        assert first_payload["model"] == "test-model"
        assert first_payload["stream"] is False
        assert first_payload["messages"][0]["role"] == "system"
        assert first_payload["messages"][1:] == expected_first
        assert second_payload["messages"][1:] == expected_history
    else:
        assert first_request.url.path == "/reply"
        assert first_payload["conversation_id"] == conversation["id"]
        assert first_payload["agent_id"] == agent["id"]
        assert first_payload["text"] == edited_transcript
        assert second_payload["text"] == "Pamiętasz poprzednią wiadomość?"
        assert first_payload["messages"] == expected_first
        assert second_payload["messages"] == expected_history
    assert speech.synthesis_calls == [(text, "gosia") for text in expected_replies]


@pytest.mark.parametrize("invalid_body", [b'{"name":', b"[", b"\xff", b"null", b"[]"])
def test_invalid_json_patch_returns_useful_error_without_changing_agent(running, invalid_body):
    client, _, _ = running
    before = client.get("/api/bootstrap").json()["agents"]
    response = client.patch(
        "/api/agents/hermes", content=invalid_body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]
    assert client.get("/api/bootstrap").json()["agents"] == before
