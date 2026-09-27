"""Same-origin local API shared by the UI, MCP and integrations."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .channels.base import encode_voice
from .dispatch import command_presets, generate_reply
from .speech import SpeechService, available_voices
from .store import Store


class AgentConfig(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["inbox", "openai", "webhook", "command"] = "inbox"
    endpoint: str = Field(default="", max_length=2000)
    model: str = Field(default="", max_length=200)
    voice: str = "sophie"
    color: str = Field(default="#eb7256", pattern=r"^#[0-9a-fA-F]{6}$")
    api_key: str = Field(default="", max_length=4096)
    command_id: str = ""
    working_directory: str = ""

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Podaj nazwę agenta.")
        return value.strip()


class ConversationInput(BaseModel):
    agent_id: str
    title: str = Field(default="Nowa rozmowa", min_length=1, max_length=100)


class MessageInput(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    audio_id: str | None = None

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Wiadomość jest pusta.")
        return value.strip()


class AgentMessageInput(MessageInput):
    agent_id: str
    conversation_id: str | None = None
    reply_to_message_id: int | None = Field(default=None, gt=0)


class SpeechInput(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    voice: str = "sophie"


class OpenAISpeechInput(BaseModel):
    input: str = Field(min_length=1, max_length=12000)
    voice: str = "sophie"
    model: str = "loudkit"
    response_format: Literal["mp3", "opus", "aac", "flac", "wav", "pcm"] = "mp3"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


def create_app(data_dir: Path | None = None, speech=None, adapter_factory=None):
    from .channels.api import mount_channel_routes
    from .channels.manager import ChannelManager
    from .channels.registry import create_adapter

    data_dir = data_dir or Path(os.environ.get("LOUDTALK_DATA_DIR", Path.home() / ".loudtalk"))
    store = Store(data_dir)
    speech = speech or SpeechService(data_dir)
    channel_manager = ChannelManager(data_dir, store, speech, adapter_factory or create_adapter)
    tasks: set[asyncio.Task] = set()
    conversation_locks: dict[str, asyncio.Lock] = {}

    def spawn(coro):
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    @asynccontextmanager
    async def lifespan(app):
        store.recover_interrupted()
        await channel_manager.start()
        yield
        await channel_manager.close()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="LoudTalk", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.speech = store, speech
    app.state.tasks = tasks
    app.state.channel_manager = channel_manager
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
    )

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if (origin and origin != expected_origin) or request.headers.get(
            "sec-fetch-site"
        ) == "cross-site":
            return JSONResponse(
                {"detail": "Otwórz LoudTalk bezpośrednio na tym komputerze."}, status_code=403
            )
        try:
            if int(request.headers.get("content-length", "0")) > 26 * 1024 * 1024:
                return JSONResponse(
                    {"detail": "Plik może mieć maksymalnie 25 MB."}, status_code=413
                )
        except ValueError:
            return JSONResponse({"detail": "Nieprawidłowa długość żądania."}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith(("/api/", "/audio/", "/v1/", "/hooks/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc.args[0])}, status_code=404)

    @app.exception_handler(FileNotFoundError)
    async def audio_missing(request, exc):
        return JSONResponse(
            {"detail": "Nie znaleziono nagrania. Nagraj je ponownie."}, status_code=404
        )

    @app.exception_handler(ValidationError)
    @app.exception_handler(RequestValidationError)
    async def invalid_fields(request, exc):
        errors = [
            f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors()
        ]
        return JSONResponse({"detail": "Sprawdź dane. " + "; ".join(errors)}, status_code=422)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(RuntimeError)
    async def unavailable(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    def validate_agent(config):
        if config["voice"] not in {v["id"] for v in available_voices()}:
            raise ValueError("Wybierz głos z listy.")
        if config["kind"] in ("openai", "webhook"):
            parsed = urlparse(config["endpoint"])
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Podaj pełny adres http:// lub https:// bez hasła w adresie.")
            if config["kind"] == "openai" and not config["model"].strip():
                raise ValueError("Podaj nazwę modelu.")
        if config["kind"] == "command":
            if config["command_id"] not in {p["id"] for p in command_presets()}:
                raise ValueError("Wybierz aplikację agenta z listy.")
            cwd = Path(config["working_directory"]).expanduser()
            if not cwd.is_absolute() or not cwd.is_dir():
                raise ValueError("Wybierz istniejący folder pracy agenta.")
            config["working_directory"] = str(cwd)
        return config

    @app.get("/api/bootstrap")
    def bootstrap():
        import shlex

        executable = str(Path(sys.executable).parent / "loudtalk")
        return {
            "agents": [store.public_agent(a) for a in store.agents()],
            "conversations": store.conversations(),
            "voices": available_voices(),
            "engine": speech.status(),
            "mcp_command": f"{shlex.quote(executable)} mcp",
            "mcp_config": {"mcpServers": {"loudtalk": {"command": executable, "args": ["mcp"]}}},
            "command_presets": command_presets(),
            "working_directory": str(Path.cwd()),
        }

    @app.post("/api/agents")
    def create_agent(body: AgentConfig):
        return store.public_agent(store.save_agent(validate_agent(body.model_dump())))

    @app.patch("/api/agents/{agent_id}")
    async def update_agent(agent_id: str, request: Request):
        current = store.agent(agent_id)
        updates = await request.json()
        if not isinstance(updates, dict):
            raise ValueError("Nieprawidłowe ustawienia.")
        allowed = set(AgentConfig.model_fields)
        # Omitted key preserves the configured secret; an explicit empty key clears it.
        merged = {**current, **{k: v for k, v in updates.items() if k in allowed}}
        config = validate_agent(AgentConfig(**merged).model_dump())
        config["id"] = agent_id
        return store.public_agent(store.save_agent(config))

    @app.post("/api/conversations")
    def create_conversation(body: ConversationInput):
        return store.create_conversation(body.agent_id, body.title)

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str):
        if any(m["status"] == "pending" for m in store.messages(conversation_id)):
            raise HTTPException(409, "Poczekaj na odpowiedź przed usunięciem rozmowy.")
        store.delete_conversation(conversation_id)
        return {"deleted": True}

    @app.get("/api/conversations/{conversation_id}/messages")
    def messages(conversation_id: str):
        return store.messages(conversation_id)

    async def finish_reply(message_id):
        message = store.message(message_id)
        conversation = store.conversation(message["conversation_id"])
        agent = store.agent(conversation["agent_id"])
        try:
            text = message["text"]
            if not text:
                history = [
                    m
                    for m in store.messages(conversation["id"])
                    if m["id"] < message_id and m["status"] == "ready"
                ]
                text = await generate_reply(
                    agent,
                    [{"role": m["role"], "content": m["text"]} for m in history],
                    conversation["id"],
                )
                store.update_message(message_id, text=text)
            audio = await asyncio.to_thread(speech.synthesize, text, agent["voice"])
            store.update_message(
                message_id,
                status="ready",
                error=None,
                audio_url=audio["audio_url"],
                duration=audio["duration"],
            )
        except asyncio.CancelledError:
            store.update_message(
                message_id, status="error", error="Odpowiedź została przerwana. Spróbuj ponownie."
            )
            raise
        except Exception as exc:
            error = (
                str(exc)
                if isinstance(exc, (ValueError, RuntimeError))
                else "Nie udało się przygotować odpowiedzi. Sprawdź połączenie i spróbuj ponownie."
            )
            store.update_message(message_id, status="error", error=error)

    @app.post("/api/conversations/{conversation_id}/messages")
    async def send_message(conversation_id: str, body: MessageInput):
        lock = conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            conversation = store.conversation(conversation_id)
            agent = store.agent(conversation["agent_id"])
            if any(m["status"] == "pending" for m in store.messages(conversation_id)):
                raise HTTPException(409, "Agent jeszcze odpowiada. Poczekaj chwilę.")
            audio_url, duration = None, None
            if body.audio_id:
                path = speech.resolve_audio(body.audio_id)
                import soundfile as sf

                duration = sf.info(str(path)).duration
                audio_url = f"/audio/{path.name}"
            message = store.add_message(conversation_id, "user", body.text, audio_url, duration)
            if agent["kind"] != "inbox":
                pending = store.add_message(conversation_id, "assistant", "", status="pending")
                spawn(finish_reply(pending["id"]))
            return message

    @app.post("/api/messages/{message_id}/retry")
    async def retry(message_id: int):
        message = store.message(message_id)
        lock = conversation_locks.setdefault(message["conversation_id"], asyncio.Lock())
        async with lock:
            message = store.message(message_id)
            if message["role"] != "assistant" or message["status"] != "error":
                raise HTTPException(409, "Można ponowić tylko nieudaną odpowiedź agenta.")
            if any(m["status"] == "pending" for m in store.messages(message["conversation_id"])):
                raise HTTPException(409, "Agent jeszcze odpowiada.")
            result = store.update_message(message_id, status="pending", error=None)
            spawn(finish_reply(message_id))
            return result

    @app.get("/api/agents/{agent_id}/inbox")
    def inbox(agent_id: str, after_id: int = 0):
        return store.inbox(agent_id, max(0, after_id))

    @app.post("/api/agent-messages")
    async def agent_message(body: AgentMessageInput):
        store.agent(body.agent_id)
        conversation = (
            store.conversation(body.conversation_id)
            if body.conversation_id
            else store.create_conversation(body.agent_id, "Wiadomość od agenta")
        )
        if conversation["agent_id"] != body.agent_id:
            raise ValueError("Ta rozmowa należy do innego agenta.")
        lock = conversation_locks.setdefault(conversation["id"], asyncio.Lock())
        async with lock:
            previous = channel_manager.previous_agent_reply(
                conversation["id"], body.reply_to_message_id
            )
            if previous is not None:
                return {**previous["message"], "delivery": previous["delivery"]}
            pending = store.add_message(
                conversation["id"], "assistant", body.text, status="pending"
            )
            await finish_reply(pending["id"])
            result = store.message(pending["id"])
            if result["status"] == "ready":
                delivery = await channel_manager.deliver_agent_message(
                    pending["id"], body.reply_to_message_id
                )
                if delivery is not None:
                    result["delivery"] = delivery
            return result

    async def transcribe_upload(file):
        incoming = data_dir / "incoming"
        incoming.mkdir(exist_ok=True, mode=0o700)
        path = incoming / (uuid.uuid4().hex + ".upload")
        try:
            count = 0
            with path.open("wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    count += len(chunk)
                    if count > 25 * 1024 * 1024:
                        raise HTTPException(413, "Plik może mieć maksymalnie 25 MB.")
                    handle.write(chunk)
            if count == 0:
                raise ValueError("Nagranie jest puste.")
            return await asyncio.to_thread(speech.transcribe, path)
        finally:
            path.unlink(missing_ok=True)
            await file.close()

    @app.post("/api/transcribe")
    async def transcribe(file: UploadFile = File(...)):
        return await transcribe_upload(file)

    @app.get("/api/engines")
    def engines():
        return speech.status()

    preparing_task = None

    @app.post("/api/engines/prepare")
    async def prepare_engines():
        nonlocal preparing_task

        async def prepare():
            try:
                await asyncio.to_thread(speech.prepare)
            except Exception:
                pass  # provider exposes each load error through status()

        if preparing_task is None or preparing_task.done():
            preparing_task = spawn(prepare())
        return speech.status()

    @app.post("/api/voice-preview")
    async def preview(body: SpeechInput):
        return await asyncio.to_thread(speech.synthesize, body.text, body.voice)

    @app.post("/v1/audio/speech")
    async def openai_speech(body: OpenAISpeechInput):
        result = await asyncio.to_thread(speech.synthesize, body.input, body.voice)
        original = speech.resolve_audio(result["audio_id"])
        if body.response_format == "wav" and body.speed == 1.0:
            return FileResponse(original, media_type="audio/wav", filename="speech.wav")
        encoded = await encode_voice(original, body.response_format, body.speed)
        media_types = {
            "mp3": "audio/mpeg",
            "opus": "audio/ogg",
            "aac": "audio/aac",
            "flac": "audio/flac",
            "wav": "audio/wav",
            "pcm": "audio/pcm",
        }
        return FileResponse(
            encoded,
            media_type=media_types[body.response_format],
            filename=f"speech{encoded.suffix}",
            background=BackgroundTask(encoded.unlink, missing_ok=True),
        )

    @app.post("/v1/audio/transcriptions")
    async def openai_transcription(
        file: UploadFile = File(...),
        model: str = Form("parakeet"),
        response_format: str = Form("json"),
    ):
        if response_format not in ("json", "text", "verbose_json"):
            raise ValueError("Dostępne formaty transkrypcji: json, text, verbose_json.")
        result = await transcribe_upload(file)
        if response_format == "text":
            return PlainTextResponse(result["text"])
        return {
            "text": result["text"],
            **({"duration": result["duration"]} if response_format == "verbose_json" else {}),
        }

    @app.get("/audio/{filename}")
    def audio_file(filename: str):
        audio_id = filename.removesuffix(".wav")
        return FileResponse(speech.resolve_audio(audio_id), media_type="audio/wav")

    static = Path(__file__).parent / "static"
    mount_channel_routes(app, channel_manager, speech, store)
    docs = Path(__file__).resolve().parents[2] / "docs"
    if docs.is_dir():
        app.mount("/docs", StaticFiles(directory=docs), name="docs")

    @app.get("/playground")
    def playground():
        return FileResponse(static / "playground.html")

    app.mount("/static", StaticFiles(directory=static), name="static")
    app.mount("/", StaticFiles(directory=static, html=True), name="ui")
    return app
