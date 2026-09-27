"""Local messenger setup endpoints and narrowly scoped signed webhook ingress."""

from __future__ import annotations

import inspect
import ipaddress
import json
import re
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictStr, field_validator

from ..native_agents import DEFAULT_BASE_URL, build_native_agent_setup, native_agent_catalog
from ..speech import available_voices
from . import registry
from .base import ChannelError

MAX_WEBHOOK_BYTES = 1024 * 1024
WEBHOOK_PLATFORMS = {"whatsapp", "slack", "imessage"}
Name = Annotated[StrictStr, Field(min_length=1, max_length=100)]
Identifier = Annotated[StrictStr, Field(min_length=1, max_length=1024)]
Secret = Annotated[StrictStr, Field(max_length=8192)]
Settings = Annotated[dict[str, JsonValue], Field(max_length=40)]
Secrets = Annotated[dict[str, Secret], Field(max_length=20)]
Allowed = Annotated[list[Identifier], Field(max_length=500)]
_LAN_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))


class SafeValidationRoute(APIRoute):
    """Validation responses must never echo a user's bot token or nested config."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError:
                raise HTTPException(422, "Sprawdź pola konfiguracji połączenia.") from None

        return handler


class ConfigFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def nonnull(cls, value):
        if value is None:
            raise ValueError("Pole nie może być puste.")
        return value

    @field_validator("settings", "secrets", check_fields=False)
    @classmethod
    def bounded_mapping(cls, value):
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) for key in value):
            raise ValueError("Nieprawidłowa nazwa ustawienia.")
        if len(json.dumps(value)) > 65_536:
            raise ValueError("Konfiguracja jest zbyt duża.")
        return value

    @field_validator("name", check_fields=False)
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Podaj nazwę połączenia.")
        return value


class ChannelCreate(ConfigFields):
    platform: Literal["telegram", "discord", "slack", "whatsapp", "imessage"]
    name: Name
    agent_id: Identifier
    settings: Settings = Field(default_factory=dict)
    secrets: Secrets = Field(default_factory=dict)
    allowed_chats: Allowed = Field(default_factory=list)
    allowed_senders: Allowed = Field(default_factory=list)
    enabled: StrictBool = False


class ChannelPatch(ConfigFields):
    name: Name | None = None
    agent_id: Identifier | None = None
    settings: Settings | None = None
    secrets: Secrets | None = None
    allowed_chats: Allowed | None = None
    allowed_senders: Allowed | None = None
    enabled: StrictBool | None = None


class NativeSetup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice: StrictStr = Field(default="gosia", max_length=100)
    base_url: StrictStr = Field(default=DEFAULT_BASE_URL, max_length=2000)


class WebhookInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_base_url: StrictStr = Field(min_length=1, max_length=2000)


def _safe_error(error, config=None):
    text = str(error)
    for secret in sorted((str(value) for value in (config or {}).get("secrets", {}).values()
                          if value), key=len, reverse=True):
        text = text.replace(secret, "[ukryte]")
    return text


async def _invoke(function, *args, config=None):
    try:
        result = function(*args)
        return await result if inspect.isawaitable(result) else result
    except KeyError:
        raise HTTPException(404, "Nie znaleziono połączenia, agenta lub prośby o połączenie.") from None
    except (ChannelError, ValueError) as error:
        raise HTTPException(400, _safe_error(error, config)) from None


def _public_base(value: str, platform: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                 and parsed.port != 0 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment and ".." not in parsed.path
                 and re.fullmatch(r"[A-Za-z0-9/_\-]*", parsed.path)
                 and not any(ord(char) < 33 for char in value))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Podaj adres HTTP(S) bez hasła, parametrów i fragmentu.")
    if parsed.scheme == "http":
        local = parsed.hostname.lower() == "localhost"
        try:
            address = ipaddress.ip_address(parsed.hostname)
            local = address.is_loopback or any(address in network for network in _LAN_NETWORKS)
        except ValueError:
            pass
        if platform != "imessage" or not local:
            raise ValueError("Webhook wymaga HTTPS; lokalny BlueBubbles może używać HTTP.")
    return value


async def _webhook_body(request: Request) -> bytes:
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(400, "Nieprawidłowa długość żądania.") from None
    if length < 0:
        raise HTTPException(400, "Nieprawidłowa długość żądania.")
    if length > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "Zdarzenie webhooka przekracza 1 MB.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "Zdarzenie webhooka przekracza 1 MB.")
        body.extend(chunk)
    return bytes(body)


def mount_channel_routes(app: FastAPI, manager, speech, store):
    router = APIRouter(route_class=SafeValidationRoute)

    async def config_for(channel_id):
        return await _invoke(manager.get_config, channel_id)

    async def validate(config):
        return await _invoke(registry.validate_channel_config, config, config=config)

    @router.get("/api/channels/bootstrap")
    async def bootstrap():
        return {
            "channels": manager.list_channels(), "catalog": registry.channel_catalog(),
            "agents": [store.public_agent(agent) for agent in store.agents()],
            "voices": available_voices(), "engine": speech.status(),
            "native_agents": native_agent_catalog(), "pairings": manager.pairings(),
            "events": manager.events(),
        }

    @router.get("/api/native-agents")
    async def native_agents():
        return native_agent_catalog()

    @router.post("/api/native-agents/{agent_id}/setup")
    async def native_setup(agent_id: str, body: NativeSetup):
        return await _invoke(build_native_agent_setup, agent_id, body.voice, body.base_url)

    @router.post("/api/channels")
    async def create_channel(body: ChannelCreate):
        config = body.model_dump()
        config = await validate(config)
        return await _invoke(manager.create, config, config=config)

    @router.patch("/api/channels/{channel_id}")
    async def update_channel(channel_id: str, body: ChannelPatch):
        config = await config_for(channel_id)
        updates = body.model_dump(exclude_unset=True)
        merged = {**config, **updates}
        for key in ("settings", "secrets"):
            if key in updates:
                values = {k: v for k, v in updates[key].items() if key != "secrets" or v}
                merged[key] = {**config.get(key, {}), **values}
        merged = await validate(merged)
        normalized = {key: merged[key] for key in updates}
        return await _invoke(manager.update, channel_id, normalized, config=merged)

    @router.post("/api/channels/{channel_id}/check")
    async def check_channel(channel_id: str):
        config = await config_for(channel_id)
        return await _invoke(manager.check, channel_id, config=config)

    @router.post("/api/channels/{channel_id}/enable")
    async def enable_channel(channel_id: str):
        config = await config_for(channel_id)
        await validate(config)
        return await _invoke(manager.enable, channel_id, config=config)

    @router.post("/api/channels/{channel_id}/disable")
    async def disable_channel(channel_id: str):
        config = await config_for(channel_id)
        return await _invoke(manager.disable, channel_id, config=config)

    @router.post("/api/channels/{channel_id}/pairings/{pairing_id}/approve")
    async def approve_pairing(channel_id: str, pairing_id: str):
        config = await config_for(channel_id)
        return await _invoke(manager.approve, channel_id, pairing_id, config=config)

    @router.post("/api/channels/{channel_id}/webhook-info")
    async def webhook_info(channel_id: str, body: WebhookInfo):
        config = await config_for(channel_id)
        platform = config["platform"]
        if platform not in WEBHOOK_PLATFORMS:
            raise HTTPException(400, "Ten komunikator nie wymaga webhooka.")
        base_url = await _invoke(_public_base, body.public_base_url, platform)
        url = f"{base_url}/hooks/{quote(channel_id, safe='')}"
        note = "Udostępnij tylko tę ścieżkę; panel i API LoudTalk pozostają lokalne."
        if platform == "imessage":
            secret = config.get("secrets", {}).get("webhook_secret", "")
            if len(secret) < 32:
                raise HTTPException(400, "Najpierw zapisz token webhooka iMessage (min. 32 znaki).")
            # This deliberately explicit response reveals only the webhook token to its owner.
            url += f"?token={quote(secret, safe='')}"
            note += " Ten adres zawiera token. Wklej go wyłącznie w webhooku BlueBubbles."
        return {"url": url, "platform": platform, "note": note,
                "events": ["new-message"] if platform == "imessage" else ["messages"]
                if platform == "whatsapp" else ["message.im", "message.channels"],
                "method": "POST", "verification_method": "GET" if platform == "whatsapp"
                else "POST"}

    @router.api_route("/hooks/{channel_id}", methods=["GET", "POST"])
    async def webhook(channel_id: str, request: Request):
        config = await config_for(channel_id)
        platform = config["platform"]
        if platform not in WEBHOOK_PLATFORMS:
            raise HTTPException(404, "To połączenie nie udostępnia webhooka.")
        if request.method == "GET" and platform != "whatsapp":
            raise HTTPException(405, "Ten webhook przyjmuje POST.", headers={"Allow": "POST"})
        raw = await _webhook_body(request)
        try:
            adapter = manager.adapter_factory(config)
            challenge = adapter.verify_webhook(raw, dict(request.headers), dict(request.query_params))
        except ChannelError:
            raise HTTPException(403, "Nieprawidłowe uwierzytelnienie webhooka.") from None
        if challenge is not None:
            if platform == "slack":
                return JSONResponse({"challenge": challenge})
            return PlainTextResponse(challenge)
        if request.method == "GET":
            raise HTTPException(400, "Brakuje danych weryfikacji webhooka.")
        if not config.get("enabled"):
            return {"ok": True, "ignored": "disabled"}
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
            events = adapter.parse_events(payload)
        except (ValueError, TypeError, KeyError, AttributeError, ChannelError):
            raise HTTPException(400, "Nieprawidłowe zdarzenie komunikatora.") from None
        try:
            for event in events:
                if await manager.accept(event) is False:
                    raise HTTPException(503, "Kolejka jest pełna. Ponów dostarczenie później.")
        except HTTPException:
            raise
        except Exception:
            # An ACK means durable acceptance. Provider retries are deduplicated by the manager.
            raise HTTPException(503, "Nie zapisano zdarzenia. Ponów dostarczenie później.") from None
        return {"ok": True}

    app.include_router(router)
