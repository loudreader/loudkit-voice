"""Messenger configuration catalog and lazy adapter construction."""

from __future__ import annotations

import copy
import re

from .base import ChannelError


def _field(key, label, *, secret=False, required=True, default=None, help="", type="text"):
    return dict(
        key=key,
        label=label,
        secret=secret,
        required=required,
        default=default,
        help=help,
        type=type,
    )


_CATALOG = [
    dict(
        id="telegram",
        name="Telegram",
        mode="polling",
        voice_note=True,
        description="Głosówki przez własnego bota. Działa bez publicznego adresu serwera.",
        docs="/docs/channels/telegram.md",
        fields=[
            _field(
                "bot_token",
                "Token bota z BotFather",
                secret=True,
                help="Użyj osobnego bota albo podłącz silniki do istniejącego Hermesa/OpenClaw.",
            ),
        ],
    ),
    dict(
        id="imessage",
        name="iMessage",
        mode="webhook",
        voice_note=False,
        description="Przez BlueBubbles na Macu zalogowanym na konto iMessage agenta.",
        docs="/docs/channels/imessage.md",
        fields=[
            _field("server_url", "Adres BlueBubbles", default="http://127.0.0.1:1234"),
            _field("password", "Hasło BlueBubbles", secret=True),
            _field(
                "webhook_secret",
                "Własny token webhooka",
                secret=True,
                help="Losowy sekret o długości co najmniej 32 znaków.",
            ),
            _field(
                "native_audio_message",
                "Natywna głosówka (Private API)",
                type="checkbox",
                required=False,
                default=False,
                help="Domyślnie odsyłamy odtwarzalny plik M4A. Wymaga działającego Private API.",
            ),
        ],
    ),
    dict(
        id="whatsapp",
        name="WhatsApp",
        mode="webhook",
        voice_note=True,
        description="Głosówki przez WhatsApp Business Cloud API i podpisany webhook.",
        docs="/docs/channels/whatsapp.md",
        fields=[
            _field("phone_number_id", "Phone Number ID"),
            _field("access_token", "Token dostępu Meta", secret=True),
            _field("app_secret", "App Secret", secret=True),
            _field("verify_token", "Własny token weryfikacji", secret=True),
            _field("graph_version", "Wersja Graph API", required=False, default="v24.0"),
        ],
    ),
    dict(
        id="discord",
        name="Discord",
        mode="gateway",
        voice_note=True,
        description="Natywne głosówki w rozmowie z botem lub wybranym kanale serwera.",
        docs="/docs/channels/discord.md",
        fields=[
            _field("bot_token", "Token bota Discord", secret=True),
            _field(
                "guild_messages",
                "Odbieraj na serwerach",
                type="checkbox",
                required=False,
                default=False,
                help="Wymaga Message Content Intent. Domyślnie tylko prywatne wiadomości.",
            ),
        ],
    ),
    dict(
        id="slack",
        name="Slack",
        mode="webhook",
        voice_note=False,
        description="Odbiera nagranie i odpowiada plikiem MP3 w tym samym wątku.",
        docs="/docs/channels/slack.md",
        fields=[
            _field("bot_token", "Bot User OAuth Token", secret=True),
            _field("signing_secret", "Signing Secret", secret=True),
            _field("team_id", "Workspace ID", required=False),
            _field("bot_user_id", "Bot User ID", required=False),
        ],
    ),
]


def channel_catalog() -> list[dict]:
    return copy.deepcopy(_CATALOG)


def validate_channel_config(config: dict) -> dict:
    """Validate a complete saved configuration, without contacting a platform."""
    item = copy.deepcopy(config)
    platform = next((p for p in _CATALOG if p["id"] == item.get("platform")), None)
    if platform is None:
        raise ValueError("Wybierz komunikator z listy.")
    name = item.get("name", platform["name"])
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise ValueError("Podaj nazwę połączenia (do 100 znaków).")
    item["name"] = name.strip()
    if not isinstance(item.get("agent_id"), str) or not item["agent_id"]:
        raise ValueError("Wybierz swojego agenta.")
    for group in ("settings", "secrets"):
        values = item.setdefault(group, {})
        if not isinstance(values, dict):
            raise ValueError("Nieprawidłowe ustawienia połączenia.")
        fields = {f["key"]: f for f in platform["fields"] if f["secret"] == (group == "secrets")}
        if set(values) - set(fields):
            raise ValueError("Nieznane ustawienie połączenia.")
        for key, field in fields.items():
            value = values.get(key, field["default"])
            if value is None:
                value = ""
            if field["type"] == "checkbox":
                if not isinstance(value, bool):
                    raise ValueError(f"{field['label']}: wybierz tak lub nie.")
            elif not isinstance(value, str) or len(value) > 8192:
                raise ValueError(f"{field['label']}: nieprawidłowa wartość.")
            else:
                value = value.strip()
            if field["required"] and not value:
                raise ValueError(f"Uzupełnij: {field['label']}.")
            values[key] = value
    for key in ("allowed_senders", "allowed_chats"):
        values = item.setdefault(key, [])
        if (
            not isinstance(values, list)
            or len(values) > 1000
            or any(not isinstance(v, str) or not v or len(v) > 1000 for v in values)
        ):
            raise ValueError("Nieprawidłowa lista dozwolonych rozmówców.")
    if item["platform"] == "imessage" and len(item["secrets"]["webhook_secret"]) < 32:
        raise ValueError("Token webhooka musi mieć co najmniej 32 znaki.")
    if item["platform"] == "whatsapp":
        if not item["settings"]["phone_number_id"].isdigit():
            raise ValueError("Phone Number ID powinien zawierać tylko cyfry.")
        if not re.fullmatch(r"v\d+\.0", item["settings"]["graph_version"]):
            raise ValueError("Wersja Graph API powinna wyglądać np. v24.0.")
    return item


def create_adapter(config: dict):
    platform = config["platform"]
    if platform == "telegram":
        from .telegram import TelegramAdapter

        return TelegramAdapter(config)
    if platform == "discord":
        from .discord import DiscordAdapter

        return DiscordAdapter(config)
    if platform == "imessage":
        from .imessage import IMessageAdapter

        return IMessageAdapter(config)
    if platform == "whatsapp":
        from .whatsapp import WhatsAppAdapter

        return WhatsAppAdapter(config)
    if platform == "slack":
        from .slack import SlackAdapter

        return SlackAdapter(config)
    raise ChannelError("Ten komunikator nie ma adaptera.")
