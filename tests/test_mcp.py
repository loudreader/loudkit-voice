"""Exercise SDK schemas, HTTP-backed tools, and both real MCP protocols."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from loudtalk.mcp import MAX_AUDIO_BYTES, build_http_app, build_server

TOOL_NAMES = {
    "list_conversations",
    "receive_voice_messages",
    "send_voice_message",
    "synthesize_speech",
    "transcribe_audio",
}


def fake_service(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/bootstrap":
        return httpx.Response(
            200,
            json={
                "agents": [{"id": "hermes", "name": "Hermes", "api_key": "private"}],
                "conversations": [{"id": "conversation-1", "agent_id": "hermes"}],
                "engine": {"tts": "ready"},
            },
        )
    if path.endswith("/inbox"):
        assert request.url.params["after_id"] == "4"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 5,
                    "conversation_id": "conversation-1",
                    "role": "user",
                    "text": "Cześć",
                    "audio_url": "/audio/recording.wav",
                    "status": "ready",
                }
            ],
        )
    if path == "/api/agent-messages":
        body = json.loads(request.content)
        assert body == {"agent_id": "hermes", "text": "Cześć!", "conversation_id": "conversation-1"}
        return httpx.Response(
            200,
            json={
                "id": 6,
                "conversation_id": body["conversation_id"],
                "text": body["text"],
                "audio_url": "/audio/reply.wav",
                "duration": 1.5,
                "status": "ready",
            },
        )
    if path == "/api/voice-preview":
        assert json.loads(request.content) == {"text": "Cześć!", "voice": "gosia"}
        return httpx.Response(
            200,
            json={
                "audio_id": "preview",
                "audio_url": "/audio/preview.wav",
                "duration": 1.4,
            },
        )
    if path == "/api/transcribe":
        assert "multipart/form-data" in request.headers["content-type"]
        assert b'name="file"; filename="note.wav"' in request.content
        assert b"RIFF-test-audio" in request.content
        return httpx.Response(
            200,
            json={
                "text": "Cześć",
                "audio_id": "recording",
                "audio_url": "/audio/recording.wav",
                "duration": 1.2,
            },
        )
    return httpx.Response(404, json={"detail": "Unknown route"})


@pytest.fixture
def server():
    return build_server(_transport=httpx.MockTransport(fake_service))


def value(result):
    """SDK v2 wraps non-object return values as {'result': ...}."""
    data = result.structured_content
    return data["result"] if set(data) == {"result"} else data


async def test_real_sdk_lists_all_tools_and_input_schemas(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) == TOOL_NAMES
    assert tools["receive_voice_messages"].input_schema["properties"]["after_id"]["default"] == 0
    assert tools["send_voice_message"].input_schema["required"] == ["agent_id", "text"]
    assert tools["synthesize_speech"].input_schema["properties"]["voice"]["default"] == "gosia"
    assert tools["list_conversations"].annotations.read_only_hint is True


async def test_list_conversations_keeps_only_public_data(server):
    result = value(await server.call_tool("list_conversations", {}))
    assert result == {
        "agents": [{"id": "hermes", "name": "Hermes"}],
        "conversations": [{"id": "conversation-1", "agent_id": "hermes"}],
    }


async def test_inbox_preserves_cursor_and_encodes_agent_id():
    seen = []

    def service(request):
        seen.append((request.url.raw_path, request.method))
        return fake_service(request)

    server = build_server(_transport=httpx.MockTransport(service))
    args = {"agent_id": "a/b?agent", "after_id": 4}
    first = value(await server.call_tool("receive_voice_messages", args))
    again = value(await server.call_tool("receive_voice_messages", args))
    assert first == again  # reading a cursor never acknowledges/deletes messages
    assert first[0]["id"] == 5
    assert first[0]["audio_url"] == "http://127.0.0.1:8765/audio/recording.wav"
    assert seen == [(b"/api/agents/a%2Fb%3Fagent/inbox?after_id=4", "GET")] * 2


async def test_send_voice_message_returns_persisted_audio(server, tmp_path, monkeypatch):
    audio = tmp_path / "audio" / "reply.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"RIFF-test-audio")
    monkeypatch.setenv("LOUDTALK_DATA_DIR", str(tmp_path))
    result = value(
        await server.call_tool(
            "send_voice_message",
            {
                "agent_id": "hermes",
                "text": "Cześć!",
                "conversation_id": "conversation-1",
            },
        )
    )
    assert result["id"] == 6 and result["status"] == "ready"
    assert result["audio_url"] == "http://127.0.0.1:8765/audio/reply.wav"
    assert result["audio_path"] == str(audio)


async def test_synthesize_and_transcribe_use_app(server, tmp_path):
    synthesized = value(await server.call_tool("synthesize_speech", {"text": "Cześć!"}))
    assert synthesized["audio_id"] == "preview"
    assert synthesized["audio_url"].endswith("/audio/preview.wav")
    audio = tmp_path / "note.wav"
    audio.write_bytes(b"RIFF-test-audio")
    transcript = value(await server.call_tool("transcribe_audio", {"path": str(audio)}))
    assert transcript["text"] == "Cześć"
    assert transcript["audio_url"] == "http://127.0.0.1:8765/audio/recording.wav"


@pytest.mark.parametrize("state", ["missing", "empty", "oversize", "directory", "fifo"])
async def test_transcribe_refuses_invalid_local_files(server, tmp_path, state):
    source = tmp_path / "bad.wav"
    if state == "empty":
        source.touch()
    elif state == "oversize":
        with source.open("wb") as handle:
            handle.truncate(MAX_AUDIO_BYTES + 1)
    elif state == "directory":
        source.mkdir()
    elif state == "fifo":
        os.mkfifo(source)
    with pytest.raises(ToolError):
        async with asyncio.timeout(2):
            await server.call_tool("transcribe_audio", {"path": str(source)})


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://127.0.0.1.evil.test",
        "http://127.0.0.1@evil.test",
        "http://user:secret@localhost:8765",
        "http://localhost:0",
        "ftp://127.0.0.1",
        "http://localhost:8765/other",
        "http://localhost:8765?override=1",
    ],
)
def test_mcp_only_connects_to_local_app(url):
    with pytest.raises(ValueError, match="loopback"):
        build_server(url)


@pytest.mark.parametrize(
    "url", ["http://localhost:8765/", "http://[::1]:8765", "http://127.0.0.1:8765"]
)
def test_loopback_forms_supported(url):
    assert build_server(url)


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, json={"id": 9, "status": "error", "error": "Synthesis failed"}),
        httpx.Response(200, json={"id": 9, "status": "pending"}),
        httpx.Response(503, json={"detail": "Start the speech engine"}),
    ],
)
async def test_send_failure_is_tool_error(reply):
    server = build_server(_transport=httpx.MockTransport(lambda _: reply))
    with pytest.raises(ToolError):
        await server.call_tool("send_voice_message", {"agent_id": "hermes", "text": "Cześć"})


async def test_unreachable_app_is_actionable_tool_error():
    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    server = build_server(_transport=httpx.MockTransport(offline))
    with pytest.raises(ToolError, match="Start the app"):
        await server.call_tool("list_conversations", {})


async def test_real_subprocess_stdio_handshake_and_tool_calls():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            result = fake_service(httpx.Request("GET", "http://localhost" + self.path))
            self.send_response(result.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(result.content)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = b'{"detail":"Speech engine is unavailable"}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    http_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=http_server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{http_server.server_port}"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from loudtalk.mcp import run_stdio; import sys; run_stdio(sys.argv[1])", url],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    try:
        async with asyncio.timeout(20):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.server_info.name == "loudtalk"
                    assert "after_id" in initialized.instructions
                    listed = await session.list_tools()
                    assert {tool.name for tool in listed.tools} == TOOL_NAMES
                    result = await session.call_tool("list_conversations", {})
                    assert not result.is_error
                    assert value(result)["agents"][0]["id"] == "hermes"
                    failure = await session.call_tool("synthesize_speech", {"text": "Cześć"})
                    assert failure.is_error
                    assert "Speech engine is unavailable" in failure.content[0].text
    finally:
        await asyncio.to_thread(http_server.shutdown)
        http_server.server_close()
        worker.join(timeout=2)


async def test_streamable_http_requires_bearer_and_initializes_real_protocol():
    token = "test-loudtalk-mcp-token-" + "a" * 32
    app = build_http_app(token=token, _transport=httpx.MockTransport(fake_service))
    headers = {"Accept": "application/json, text/event-stream"}
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "loudtalk-tests", "version": "1"},
        },
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8766",
        ) as client:
            for bad_headers in [headers, {**headers, "Authorization": "Bearer incorrect"}]:
                response = await client.post("/mcp", headers=bad_headers, json=initialize)
                assert response.status_code == 401
                assert response.headers["www-authenticate"] == "Bearer"
            authorized = {**headers, "Authorization": "Bearer " + token}
            response = await client.post("/mcp", headers=authorized, json=initialize)
            assert response.status_code == 200, response.text
            assert response.json()["result"]["serverInfo"]["name"] == "loudtalk"
            response = await client.post(
                "/mcp",
                headers=authorized,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "list_conversations",
                        "arguments": {},
                    },
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["result"]["structuredContent"]["agents"][0]["id"] == "hermes"


@pytest.mark.parametrize("token", ["", "short", "a" * 32 + "\n", "ó" * 40])
def test_http_requires_valid_token(token):
    with pytest.raises(ValueError, match="LOUDTALK_MCP_TOKEN"):
        build_http_app(token=token)
