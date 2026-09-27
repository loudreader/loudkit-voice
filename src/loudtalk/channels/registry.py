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
        description="Voice messages through your own bot. No public server address needed.",
        docs="/docs/channels/telegram.md",
        fields=[
            _field(
                "bot_token",
                "Bot token from BotFather",
                secret=True,
                help="Use a separate bot, or connect the speech engines to your existing Hermes/OpenClaw agent.",
            ),
        ],
    ),
    dict(
        id="imessage",
        name="iMessage",
        mode="webhook",
        voice_note=False,
        description="Through BlueBubbles on a Mac signed in to the agent’s iMessage account.",
        docs="/docs/channels/imessage.md",
        fields=[
            _field("server_url", "BlueBubbles address", default="http://127.0.0.1:1234"),
            _field("password", "BlueBubbles password", secret=True),
            _field(
                "webhook_secret",
                "Your webhook token",
                secret=True,
                help="A random secret with at least 32 characters.",
            ),
            _field(
                "native_audio_message",
                "Native voice message (Private API)",
                type="checkbox",
                required=False,
                default=False,
                help="The default is a playable M4A file. Native voice messages require the Private API.",
            ),
        ],
    ),
    dict(
        id="whatsapp",
        name="WhatsApp",
        mode="webhook",
        voice_note=True,
        description="Voice messages through WhatsApp Business Cloud API and a signed webhook.",
        docs="/docs/channels/whatsapp.md",
        fields=[
            _field("phone_number_id", "Phone Number ID"),
            _field("access_token", "Meta access token", secret=True),
            _field("app_secret", "App Secret", secret=True),
            _field("verify_token", "Your verification token", secret=True),
            _field("graph_version", "Graph API version", required=False, default="v24.0"),
        ],
    ),
    dict(
        id="discord",
        name="Discord",
        mode="gateway",
        voice_note=True,
        description="Native voice messages in a bot chat or a selected server channel.",
        docs="/docs/channels/discord.md",
        fields=[
            _field("bot_token", "Discord bot token", secret=True),
            _field(
                "guild_messages",
                "Receive messages on servers",
                type="checkbox",
                required=False,
                default=False,
                help="Requires Message Content Intent. Only direct messages are enabled by default.",
            ),
        ],
    ),
    dict(
        id="slack",
        name="Slack",
        mode="webhook",
        voice_note=False,
        description="Receives a recording and replies with an MP3 file in the same thread.",
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
        raise ValueError("Choose a messenger from the list.")
    name = item.get("name", platform["name"])
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise ValueError("Enter a connection name (up to 100 characters).")
    item["name"] = name.strip()
    if not isinstance(item.get("agent_id"), str) or not item["agent_id"]:
        raise ValueError("Choose your agent.")
    for group in ("settings", "secrets"):
        values = item.setdefault(group, {})
        if not isinstance(values, dict):
            raise ValueError("Invalid connection settings.")
        fields = {f["key"]: f for f in platform["fields"] if f["secret"] == (group == "secrets")}
        if set(values) - set(fields):
            raise ValueError("Unknown connection setting.")
        for key, field in fields.items():
            value = values.get(key, field["default"])
            if value is None:
                value = ""
            if field["type"] == "checkbox":
                if not isinstance(value, bool):
                    raise ValueError(f"{field['label']}: choose yes or no.")
            elif not isinstance(value, str) or len(value) > 8192:
                raise ValueError(f"{field['label']}: invalid value.")
            else:
                value = value.strip()
            if field["required"] and not value:
                raise ValueError(f"Required: {field['label']}.")
            values[key] = value
    for key in ("allowed_senders", "allowed_chats"):
        values = item.setdefault(key, [])
        if (
            not isinstance(values, list)
            or len(values) > 1000
            or any(not isinstance(v, str) or not v or len(v) > 1000 for v in values)
        ):
            raise ValueError("Invalid list of allowed contacts.")
    if item["platform"] == "imessage" and len(item["secrets"]["webhook_secret"]) < 32:
        raise ValueError("The webhook token must have at least 32 characters.")
    if item["platform"] == "whatsapp":
        if not item["settings"]["phone_number_id"].isdigit():
            raise ValueError("Phone Number ID must contain digits only.")
        if not re.fullmatch(r"v\d+\.0", item["settings"]["graph_version"]):
            raise ValueError("Use a Graph API version such as v24.0.")
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
    raise ChannelError("No adapter is available for this messenger.")
