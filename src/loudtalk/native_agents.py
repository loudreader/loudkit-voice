"""Voice-provider setup for agents that already own their messaging connections.

These are configuration fragments, never new bots. Generating a setup does not
read or modify personal agent configuration, register webhooks, or start pollers.
"""

from __future__ import annotations

import copy
import json
from urllib.parse import urlsplit

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
            "description": "Dodaj Loudkit i Parakeet do bota, którego już używasz.",
            "channels": [
                _channel("telegram", "voice_note", True, "W czacie włącz /voice on."),
                _channel("discord", "voice_note", True, "W czacie włącz /voice on."),
                _channel(
                    "whatsapp", "audio_attachment", None,
                    "Odbiór głosówek i narzędzie TTS. Automatyczne odpowiedzi wymagają "
                    "sprawdzenia w Twojej wersji Hermes.",
                ),
                _channel(
                    "slack", "audio_attachment", None,
                    "Odbiór głosówek i narzędzie TTS. Automatyczne odpowiedzi wymagają "
                    "sprawdzenia w Twojej wersji Hermes.",
                ),
            ],
            "verification": "upstream_source",
            "verified_at": VERIFIED_AT,
        },
        {
            "id": "openclaw",
            "name": "OpenClaw",
            "description": "Jeden dostawca głosu dla komunikatorów Twojego OpenClaw.",
            "channels": [
                _channel("telegram", "voice_note", True),
                _channel("whatsapp", "voice_note", True),
                _channel("discord", "audio_attachment", True),
                _channel("slack", "audio_attachment", True),
                _channel(
                    "imessage", "audio_attachment", True,
                    "Przez już połączony BlueBubbles. Zwykłe auto-TTS wysyła plik audio; "
                    "natywna notatka głosowa wymaga asVoice w narzędziu BlueBubbles.",
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
        raise ValueError("Podaj poprawny lokalny adres API LoudTalk.") from exc
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
            "Użyj lokalnego adresu kończącego się na /v1, np. "
            "http://127.0.0.1:8765/v1. Agent na innym komputerze potrzebuje tunelu."
        )
    return url


def build_native_agent_setup(
    agent_id: str, voice: str = "gosia", base_url: str = DEFAULT_BASE_URL
) -> dict:
    """Build a source-backed, reviewable setup without touching the user's bot."""
    if voice not in {"gosia", "darkman"}:
        raise ValueError("Wybierz głos gosia lub darkman.")
    base_url = _validate_base_url(base_url)
    catalog = {entry["id"]: entry for entry in native_agent_catalog()}
    if agent_id not in catalog:
        raise ValueError("Ten agent nie ma jeszcze gotowego ustawienia dostawcy głosu.")

    setup = copy.deepcopy(catalog[agent_id])
    setup.update(
        base_url=base_url,
        voice=voice,
        connection_status="not_checked",
        topology="existing_agent_voice_provider",
        prerequisites=[
            "LoudTalk działa, a modele mowy są gotowe.",
            "Twój agent ma już działające połączenie z wybranym komunikatorem.",
            "Agent ma dostęp do tego lokalnego adresu; 127.0.0.1 oznacza komputer agenta.",
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
                "Dodaj poniższe sekcje stt i tts do konfiguracji aktywnego profilu Hermes. "
                "Jeśli już istnieją, zmień wskazane pola wewnątrz nich; zachowaj resztę pliku.",
                "Uruchom ponownie istniejącą bramkę Hermes, aby wczytała dostawcę głosu.",
                "W swoim czacie Telegram lub Discord wpisz /voice on, a potem nagraj głosówkę.",
            ],
            chat_commands=["/voice on", "/voice status", "/voice off"],
            notes=[
                "Hermes odbiera wiadomości i wykonuje polecenia z własną pamięcią, "
                "narzędziami i uprawnieniami. LoudTalk dostarcza rozpoznawanie i syntezę mowy.",
                "loudtalk-local to lokalna wartość wymagana przez klienta API, a nie klucz OpenAI.",
                "W Slack i WhatsApp dostępność automatycznej odpowiedzi głosowej zależy "
                "od wersji bramki. Sama konfiguracja dostawcy tego nie potwierdza.",
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
                "Dodaj lokalny profil klucza audio dla każdego agenta odbierającego głosówki: "
                "openclaw models auth paste-api-key --agent NAZWA_AGENTA --provider openai "
                "--profile-id openai:loudtalk. W pytaniu o klucz wpisz loudtalk-local.",
                "Jeśli używasz OpenAI do rozmów, zachowaj jego obecny profil jako pierwszy "
                "w kolejności uwierzytelniania. Profil openai:loudtalk wybiera tylko wpis audio.",
                "Połącz poniższy fragment z konfiguracją OpenClaw. Zastąp dotychczasowe "
                "wpisy modeli audio; zachowaj wpisy obrazu i wideo oraz pozostałe ustawienia.",
                "Uruchom ponownie istniejącą bramkę OpenClaw, a potem wyślij głosówkę "
                "w swoim komunikatorze. Ustawienie inbound odpowiada głosem na głosówkę.",
            ],
            chat_commands=["/tts status", "/tts chat default", "/tts off"],
            notes=[
                "Konfiguracja jest dla aktualnego OpenClaw: tts znajduje się na głównym "
                "poziomie, a ustawienia silnika w tts.providers.openai.",
                "Nie ustawiaj models.providers.openai.apiKey na loudtalk-local: to zmieniłoby "
                "uwierzytelnianie zwykłych rozmów, a nie tylko audio.",
                "Na iMessage wymagany jest wcześniej połączony BlueBubbles. Discord, Slack "
                "i zwykłe auto-TTS iMessage dostają odtwarzalny załącznik audio.",
                "Zapisane preferencje /tts lub ustawienia głosu konkretnego agenta mogą "
                "nadpisywać ustawienia globalne. Sprawdź /tts status w danym czacie.",
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
