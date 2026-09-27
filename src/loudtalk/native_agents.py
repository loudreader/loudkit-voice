"""Voice-provider setup for agents that already own their messaging connections.

These are configuration fragments, never new bots. Generating a setup does not
read or modify personal agent configuration, register webhooks, or start pollers.
"""

from __future__ import annotations

import copy
import json
from urllib.parse import urlsplit

from .speech import available_voices

DEFAULT_BASE_URL = "http://127.0.0.1:8765/v1"
LOCAL_API_KEY = "loudtalk-local"
VERIFIED_AT = "2026-09-22"

HERMES_SOURCES = [
    {
        "title": "Hermes: routing STT to a compatible server",
        "url": "https://github.com/NousResearch/hermes-agent/blob/main/tools/transcription_cloud.py",
    },
    {
        "title": "Hermes: OpenAI-compatible TTS implementation",
        "url": "https://github.com/NousResearch/hermes-agent/blob/main/tools/tts_tool_openai.py",
    },
    {
        "title": "Hermes: voice replies and channel commands",
        "url": "https://hermes-agent.nousresearch.com/docs/user-guide/features/voice-mode",
    },
    {
        "title": "Hermes: voice-message delivery and transcription",
        "url": "https://hermes-agent.nousresearch.com/docs/user-guide/features/tts",
    },
]
OPENCLAW_SOURCES = [
    {"title": "OpenClaw: audio transcription", "url": "https://docs.openclaw.ai/nodes/audio"},
    {
        "title": "OpenClaw: TTS configuration",
        "url": "https://docs.openclaw.ai/tools/tts/configuration",
    },
    {
        "title": "OpenClaw: TTS field reference",
        "url": "https://docs.openclaw.ai/tools/tts/field-reference",
    },
    {"title": "OpenClaw: output formats", "url": "https://docs.openclaw.ai/tools/tts/output"},
    {"title": "OpenClaw: iMessage", "url": "https://docs.openclaw.ai/channels/bluebubbles"},
]


def _channel(id: str, output: str, automatic_reply: bool | None, note: str = "") -> dict:
    return {
        "id": id,
        "input": True,
        "output": output,
        "automatic_reply": automatic_reply,
        "note": note,
    }


def native_agent_catalog() -> list[dict]:
    """Return documented capabilities, explicitly distinct from connection status."""
    return [
        {
            "id": "hermes",
            "name": "Hermes",
            "description": "Add Loudkit and Parakeet to the bot you already use.",
            "channels": [
                _channel("telegram", "voice_note", True, "Enable /voice on in your chat."),
                _channel("discord", "voice_note", True, "Enable /voice on in your chat."),
                _channel(
                    "whatsapp", "audio_attachment", None,
                    "Voice message input and a TTS tool are available. Check automatic replies "
                    "in your version of Hermes.",
                ),
                _channel(
                    "slack", "audio_attachment", None,
                    "Voice message input and a TTS tool are available. Check automatic replies "
                    "in your version of Hermes.",
                ),
            ],
            "verification": "upstream_source",
            "verified_at": VERIFIED_AT,
        },
        {
            "id": "openclaw",
            "name": "OpenClaw",
            "description": "One voice provider for your OpenClaw messengers.",
            "channels": [
                _channel("telegram", "voice_note", True),
                _channel("whatsapp", "voice_note", True),
                _channel("discord", "audio_attachment", True),
                _channel("slack", "audio_attachment", True),
                _channel(
                    "imessage", "audio_attachment", True,
                    "Requires an existing BlueBubbles connection. Standard auto-TTS sends an audio file; "
                    "a native voice message requires asVoice in the BlueBubbles tool.",
                ),
            ],
            "verification": "upstream_source",
            "verified_at": VERIFIED_AT,
        },
    ]


def _validate_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Enter a valid local Loudkit Voice API address.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/v1"
        or port == 0
        or any(char.isspace() for char in url)
    ):
        raise ValueError(
            "Use a local address ending in /v1, such as "
            "http://127.0.0.1:8765/v1. An agent on another computer needs a tunnel."
        )
    return url


def build_native_agent_setup(
    agent_id: str, voice: str = "sophie", base_url: str = DEFAULT_BASE_URL
) -> dict:
    """Build a source-backed, reviewable setup without touching the user's bot."""
    if voice not in {item["id"] for item in available_voices()}:
        raise ValueError("Choose an available Loudkit voice.")
    base_url = _validate_base_url(base_url)
    catalog = {entry["id"]: entry for entry in native_agent_catalog()}
    if agent_id not in catalog:
        raise ValueError("This agent does not have a voice provider preset yet.")

    setup = copy.deepcopy(catalog[agent_id])
    setup.update(
        base_url=base_url,
        voice=voice,
        connection_status="not_checked",
        topology="existing_agent_voice_provider",
        prerequisites=[
            "Loudkit Voice is running and the speech models are ready.",
            "Your agent already has a working connection to the chosen messenger.",
            "Your agent can reach this local address; 127.0.0.1 refers to the agent’s computer.",
        ],
    )
    if agent_id == "hermes":
        config = {
            "stt": {
                "provider": "openai",
                "use_gateway": False,
                "openai": {
                    "base_url": base_url,
                    "api_key": LOCAL_API_KEY,
                    "model": "parakeet",
                    "timeout": 300,
                    "max_retries": 0,
                },
            },
            "tts": {
                "provider": "openai",
                "use_gateway": False,
                "openai": {
                    "base_url": base_url,
                    "api_key": LOCAL_API_KEY,
                    "model": "loudkit",
                    "voice": voice,
                    "speed": 1.0,
                },
            },
        }
        setup.update(
            config=config,
            files=[{
                "path": "~/.hermes/config.yaml",
                "format": "yaml",
                "merge": True,
                "content": _hermes_yaml(config),
            }],
            steps=[
                "Add the stt and tts sections below to the active Hermes profile configuration. "
                "If they already exist, update those fields and keep the rest of the file.",
                "Restart your existing Hermes gateway to load the voice provider.",
                "Enter /voice on in your Telegram or Discord chat, then record a voice message.",
            ],
            chat_commands=["/voice on", "/voice status", "/voice off"],
            notes=[
                "Hermes receives messages and acts with its own memory, "
                "tools and permissions. Loudkit Voice provides speech recognition and synthesis.",
                "loudtalk-local is a local placeholder required by the API client, not an OpenAI key.",
                "Automatic voice replies in Slack and WhatsApp depend "
                "on your gateway version. Provider configuration alone does not verify support.",
            ],
            sources=copy.deepcopy(HERMES_SOURCES),
        )
    else:
        config = {
            "tools": {
                "media": {
                    "models": [{
                        "provider": "openai",
                        "model": "parakeet",
                        "profile": "openai:loudtalk",
                        "baseUrl": base_url,
                        "capabilities": ["audio"],
                        "timeoutSeconds": 300,
                    }],
                    "audio": {"enabled": True},
                },
            },
            "tts": {
                "auto": "inbound",
                "provider": "openai",
                "timeoutMs": 300000,
                "modelOverrides": {"enabled": False},
                "providers": {
                    "openai": {
                        "baseUrl": base_url,
                        "apiKey": LOCAL_API_KEY,
                        "model": "loudkit",
                        "voice": voice,
                    },
                },
            },
        }
        setup.update(
            config=config,
            files=[{
                "path": "~/.openclaw/openclaw.json",
                "format": "json",
                "merge": True,
                "content": json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            }],
            steps=[
                "Add a local audio key profile for each agent receiving voice messages: "
                "openclaw models auth paste-api-key --agent AGENT_NAME --provider openai "
                "--profile-id openai:loudtalk. Enter loudtalk-local when prompted for the key.",
                "If you use OpenAI for chat, keep its existing profile first "
                "in the authentication order. The openai:loudtalk profile applies only to the audio entry.",
                "Merge the snippet below into your OpenClaw configuration. Replace the existing "
                "audio model entries; keep image and video entries and all other settings.",
                "Restart your existing OpenClaw gateway, then send a voice message "
                "in your messenger. The inbound setting replies to voice messages with audio.",
            ],
            chat_commands=["/tts status", "/tts chat default", "/tts off"],
            notes=[
                "This configuration uses the current OpenClaw structure: tts is at the top "
                "level, with engine settings in tts.providers.openai.",
                "Do not set models.providers.openai.apiKey to loudtalk-local: that would change "
                "authentication for regular chat, not just audio.",
                "iMessage requires an existing BlueBubbles connection. Discord, Slack "
                "and standard iMessage auto-TTS receive a playable audio attachment.",
                "Saved /tts preferences or agent-specific voice settings may "
                "override global settings. Check /tts status in that chat.",
            ],
            sources=copy.deepcopy(OPENCLAW_SOURCES),
        )
    return setup


def _hermes_yaml(config: dict) -> str:
    """Serialize this deliberately small mapping without a YAML dependency."""
    lines = []
    for section, values in config.items():
        lines.append(f"{section}:")
        for key, value in values.items():
            if isinstance(value, dict):
                lines.append(f"  {key}:")
                lines.extend(
                    f"    {nested_key}: {json.dumps(nested_value, ensure_ascii=False)}"
                    for nested_key, nested_value in value.items()
                )
            else:
                lines.append(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"
