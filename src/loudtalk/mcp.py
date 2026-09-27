"""MCP tools for the running LoudTalk app; speech engines live only in that app."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import mimetypes
import os
import stat
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

DEFAULT_BASE_URL = "http://127.0.0.1:8765"
MAX_AUDIO_BYTES = 25 * 1024 * 1024
REQUEST_TIMEOUT = 300.0


def _local_base_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        local = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
        port = parsed.port
    except (ValueError, TypeError):
        local, port = False, None
    if (
        not local
        or parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise ValueError("LoudTalk MCP requires a loopback app URL, e.g. http://127.0.0.1:8765")
    return value.rstrip("/")


def _audio_result(result: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Keep the persisted record, making its audio usable outside the browser."""
    result = dict(result)
    if result.get("audio_url"):
        result["audio_url"] = urljoin(base_url + "/", result["audio_url"])
        data_dir = os.environ.get("LOUDTALK_DATA_DIR")
        url = urlsplit(result["audio_url"])
        if data_dir and url.netloc == urlsplit(base_url).netloc:
            audio_dir = (Path(data_dir).expanduser() / "audio").resolve()
            filename = unquote(url.path.removeprefix("/audio/"))
            candidate = audio_dir / filename
            if (
                url.path.startswith("/audio/")
                and filename == Path(filename).name
                and not candidate.is_symlink()
                and candidate.is_file()
                and candidate.resolve().parent == audio_dir
            ):
                result["audio_path"] = str(candidate)
    return result


def _read_audio(path: str) -> tuple[str, bytes, str]:
    source = Path(path).expanduser()
    # NONBLOCK plus fstat refuses pipes/devices without hanging the MCP process.
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ToolError("Choose a regular audio file.")
            if info.st_size > MAX_AUDIO_BYTES:
                raise ToolError("Audio files must be at most 25 MiB.")
            contents = handle.read(MAX_AUDIO_BYTES + 1)
    except OSError as exc:
        raise ToolError(f"Cannot read the audio file: {exc.strerror or exc}") from exc
    if len(contents) > MAX_AUDIO_BYTES:
        raise ToolError("Audio files must be at most 25 MiB.")
    if not contents:
        raise ToolError("The audio file is empty.")
    return source.name, contents, mimetypes.guess_type(source.name)[0] or "application/octet-stream"


def build_server(
    base_url: str = DEFAULT_BASE_URL, *, _transport: httpx.AsyncBaseTransport | None = None
) -> MCPServer:
    """Connect agents to one local LoudTalk instance, without loading models.

    ``_transport`` allows integration tests to exercise real SDK tools against a
    controlled HTTP service. It is never exposed as an MCP tool argument.
    """
    base_url = _local_base_url(base_url)
    server = MCPServer(
        name="loudtalk",
        title="LoudTalk",
        version="0.1.0",
        description="Read messenger voice transcripts and reply with Loudkit speech through LoudTalk.",
        instructions=(
            "LoudTalk connects this agent to paired messenger chats or a local playground. "
            "Start with list_conversations "
            "to find your agent_id and conversation_id. receive_voice_messages returns the "
            "human's messages and their transcripts; reading never deletes them. Remember "
            "the greatest returned message id, and pass it as after_id on your next poll. "
            "An empty list means no new messages; keep your previous cursor. At most 100 "
            "messages are returned per poll, so continue after the last id to catch up. "
            "Reply with send_voice_message using the same conversation_id and the incoming "
            "message id as reply_to_message_id. That ID is required for messenger replies and "
            "keeps retries attached to the same voice command. It creates real "
            "speech and delivers it to the linked messenger when that conversation has a "
            "pending voice command. Inspect delivery.status: only sent confirms messenger "
            "delivery. Do not resend after a timeout or uncertain result before checking the chat. "
            "Local playground replies are saved in the app. These tools do not wake an idle agent. "
            "synthesize_speech creates an audio file without sending a message. "
            "The app must be running (loudtalk). These tools do not start another engine."
        ),
    )

    async def request(method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with httpx.AsyncClient(
                    base_url=base_url,
                    timeout=REQUEST_TIMEOUT,
                    transport=_transport,
                    trust_env=False,
                    follow_redirects=False,
                ) as client:
                    response = await client.request(method, path, **kwargs)
            if not response.is_success:
                try:
                    detail = response.json().get("detail", response.reason_phrase)
                except (ValueError, AttributeError):
                    detail = response.reason_phrase
                raise ToolError(f"LoudTalk returned HTTP {response.status_code}: {detail}")
            result = response.json()
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ToolError(
                "LoudTalk took longer than 300 seconds. Check the app before retrying: "
                "a sent message may still finish there."
            ) from exc
        except httpx.RequestError as exc:
            raise ToolError(
                f"Cannot reach LoudTalk at {base_url}. Start the app with `loudtalk` "
                "and check that its URL matches this MCP configuration."
            ) from exc
        except ValueError as exc:
            raise ToolError("LoudTalk returned an invalid JSON response.") from exc
        if isinstance(result, dict) and result.get("status") == "error":
            message_id = result.get("id")
            suffix = f" (saved message {message_id}; retry it in the app)" if message_id else ""
            raise ToolError(f"{result.get('error') or 'Speech generation failed'}{suffix}")
        if isinstance(result, dict) and result.get("delivery") is not None:
            delivery = result["delivery"]
            if not isinstance(delivery, dict) or delivery.get("status") != "sent":
                message_id = result.get("id")
                event_id = delivery.get("event_id") if isinstance(delivery, dict) else None
                raise ToolError(
                    "Messenger delivery is not confirmed. The reply may have arrived; "
                    "check the chat and LoudTalk activity before resending "
                    f"(saved message {message_id}, event {event_id})."
                )
        return result

    @server.tool(
        title="List agents and conversations",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def list_conversations() -> dict[str, Any]:
        """Find agent IDs and conversation IDs. Agent API keys are never included."""
        data = await request("GET", "/api/bootstrap")
        return {
            "agents": [
                {k: v for k, v in agent.items() if k != "api_key"} for agent in data["agents"]
            ],
            "conversations": data["conversations"],
        }

    @server.tool(
        title="Receive voice messages",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def receive_voice_messages(agent_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        """Read up to 100 human messages with transcripts, without deleting them.

        Save max(message['id']) as your next after_id. An empty list keeps the
        previous cursor. When replying, use the conversation_id and pass the incoming
        message's id as reply_to_message_id.
        """
        if not agent_id.strip() or after_id < 0:
            raise ToolError("Provide an agent_id and a nonnegative after_id.")
        messages = await request(
            "GET", f"/api/agents/{quote(agent_id, safe='')}/inbox", params={"after_id": after_id}
        )
        return [_audio_result(message, base_url) for message in messages]

    @server.tool(
        title="Send a voice message",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
    )
    async def send_voice_message(
        agent_id: str,
        text: str,
        conversation_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Reply with this agent's Loudkit voice in the incoming messenger conversation.

        Supply the incoming conversation_id and its message id as reply_to_message_id.
        Messenger replies require both. Reusing that message id returns the saved reply
        without sending another message, even when other voice commands are waiting.
        Only delivery.status='sent' confirms external delivery. A saved audio file
        without delivery is local playback only. Do not resend an uncertain reply
        before inspecting the chat. Omitting conversation_id creates a local conversation.
        """
        payload = {
            "agent_id": agent_id,
            "text": text,
            "conversation_id": conversation_id,
        }
        if reply_to_message_id is not None:
            if reply_to_message_id <= 0:
                raise ToolError("reply_to_message_id must be the positive incoming message id.")
            payload["reply_to_message_id"] = reply_to_message_id
        result = await request(
            "POST",
            "/api/agent-messages",
            json=payload,
        )
        if result.get("status") != "ready" or not result.get("audio_url"):
            raise ToolError("The message is saved but its audio is not ready. Check it in the app.")
        return _audio_result(result, base_url)

    @server.tool(
        title="Synthesize speech",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
    )
    async def synthesize_speech(text: str, voice: str = "sophie") -> dict[str, Any]:
        """Create a real WAV file with Loudkit; returns audio_id, duration and audio_url.

        This does not send a message. Use send_voice_message to speak in the app.
        """
        return _audio_result(
            await request("POST", "/api/voice-preview", json={"text": text, "voice": voice}),
            base_url,
        )

    @server.tool(
        title="Transcribe audio",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
    )
    async def transcribe_audio(path: str) -> dict[str, Any]:
        """Transcribe a local audio file with Parakeet (up to 25 MiB / 3 minutes).

        Accepts WAV, MP3, M4A, OGG, FLAC or WebM. Returns text, duration and the
        normalized audio URL. This does not send a message to a conversation.
        """
        upload = await asyncio.to_thread(_read_audio, path)
        return _audio_result(
            await request("POST", "/api/transcribe", files={"file": upload}), base_url
        )

    return server


def run_stdio(base_url: str = DEFAULT_BASE_URL) -> None:
    """Serve MCP over the agent's stdio connection, never an unauthenticated port."""
    build_server(base_url).run(transport="stdio")


def build_http_app(
    base_url: str = DEFAULT_BASE_URL,
    *,
    token: str,
    _transport: httpx.AsyncBaseTransport | None = None,
):
    """Authenticated /mcp endpoint for clients that require Streamable HTTP.

    Any external HTTPS reverse proxy must preserve Authorization and rewrite
    the upstream Host to 127.0.0.1:8766. The SDK's DNS-rebinding protection stays
    enabled. Treat the token as access to all conversations and local audio.
    """
    from starlette.responses import JSONResponse

    if len(token) < 32 or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError(
            "LOUDTALK_MCP_TOKEN must contain at least 32 non-whitespace ASCII characters."
        )
    expected = ("Bearer " + token).encode("ascii")

    class BearerAuth:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                headers = [v for k, v in scope["headers"] if k.lower() == b"authorization"]
                if len(headers) != 1 or not hmac.compare_digest(headers[0], expected):
                    response = JSONResponse(
                        {"detail": "A valid Bearer token is required."},
                        status_code=401,
                        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
                    )
                    await response(scope, receive, send)
                    return
            await self.app(scope, receive, send)

    app = build_server(base_url, _transport=_transport).streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        host="127.0.0.1",
    )
    app.add_middleware(BearerAuth)
    return app


def run_http(base_url: str = DEFAULT_BASE_URL, port: int = 8766, *, token: str) -> None:
    """Bind authenticated MCP only to loopback; public HTTPS is user configured."""
    import uvicorn

    uvicorn.run(build_http_app(base_url, token=token), host="127.0.0.1", port=port)
