"""Contracts for provider setup: route voice without taking over messaging or LLM auth."""

import json
from pathlib import Path

import pytest

from loudtalk.native_agents import build_native_agent_setup, native_agent_catalog
from loudtalk.speech import available_voices


def test_hermes_scopes_routes_to_audio_and_disables_managed_gateway():
    setup = build_native_agent_setup("hermes", "darkman", "http://localhost:18765/v1/")
    config = setup["config"]
    assert set(config) == {"stt", "tts"}
    for section in ("stt", "tts"):
        assert config[section]["provider"] == "openai"
        assert config[section]["use_gateway"] is False
        assert config[section]["openai"]["base_url"] == "http://localhost:18765/v1"
        assert config[section]["openai"]["api_key"] == "loudtalk-local"
    assert config["stt"]["openai"]["model"] == "parakeet"
    assert config["tts"]["openai"]["model"] == "loudkit"
    assert config["tts"]["openai"]["voice"] == "darkman"
    assert "/voice on" in setup["chat_commands"]
    assert "use_gateway: false" in setup["files"][0]["content"]


def test_openclaw_audio_profile_never_changes_text_model_credentials():
    setup = build_native_agent_setup("openclaw")
    config = json.loads(setup["files"][0]["content"])
    assert set(config) == {"tts", "tools"}
    assert "models" not in config  # Provider-wide keys would break normal agent inference.
    media = config["tools"]["media"]
    assert media["models"][0]["profile"] == "openai:loudtalk"
    assert media["models"][0]["capabilities"] == ["audio"]
    assert media["models"][0]["model"] == "parakeet"
    tts = config["tts"]
    assert tts["auto"] == "inbound"
    assert tts["providers"]["openai"]["voice"] == "sophie"
    assert tts["providers"]["openai"]["baseUrl"] == media["models"][0]["baseUrl"]
    assert "responseFormat" not in tts["providers"]["openai"]  # Channel selects Opus or MP3.
    assert any("paste-api-key" in step for step in setup["steps"])


def test_setup_is_not_a_connection_claim_and_never_reads_personal_files(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("setup generation must not access personal configuration")

    monkeypatch.setattr(Path, "open", denied)
    for entry in native_agent_catalog():
        setup = build_native_agent_setup(entry["id"])
        assert setup["connection_status"] == "not_checked"
        assert setup["verification"] == "upstream_source"
        assert setup["topology"] == "existing_agent_voice_provider"
        assert all(file["merge"] for file in setup["files"])
        assert "channels" not in setup["config"]  # No bot token, webhook or second receiver.


def test_documented_channel_limits_remain_visible():
    entries = {entry["id"]: entry for entry in native_agent_catalog()}
    hermes = {channel["id"]: channel for channel in entries["hermes"]["channels"]}
    assert hermes["telegram"]["automatic_reply"] is True
    assert hermes["slack"]["automatic_reply"] is None
    assert hermes["whatsapp"]["automatic_reply"] is None
    assert "imessage" not in hermes
    openclaw = {channel["id"]: channel for channel in entries["openclaw"]["channels"]}
    assert set(openclaw) == {"telegram", "discord", "slack", "whatsapp", "imessage"}
    assert openclaw["slack"]["output"] == "audio_attachment"
    assert openclaw["imessage"]["output"] == "audio_attachment"


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1",  # Never generate a fake local key aimed at a cloud service.
    "http://127.0.0.1:8765/v1\nstt: surprise",
    "http://secret@localhost:8765/v1",
    "http://localhost:8765/v1?api_key=secret",
    "http://localhost:8765/v1#hidden",
    "http://localhost:8765/audio/speech",
    "file:///v1",
    "http://localhost:0/v1",
    "http://localhost:65536/v1",
])
def test_invalid_provider_address_is_rejected(url):
    with pytest.raises(ValueError):
        build_native_agent_setup("hermes", base_url=url)


@pytest.mark.parametrize("agent,voice", [("mystery-bot", "gosia"), ("hermes", "alloy")])
def test_unknown_agent_or_voice_has_no_fabricated_preset(agent, voice):
    with pytest.raises(ValueError):
        build_native_agent_setup(agent, voice)


def test_generated_and_checked_in_templates_match():
    root = Path(__file__).resolve().parent.parent / "integrations" / "native"
    for agent, filename in [("hermes", "hermes.yaml"), ("openclaw", "openclaw.json")]:
        assert (root / filename).read_text() == build_native_agent_setup(agent)["files"][0]["content"]


def test_catalog_and_setups_are_independent_values():
    first = build_native_agent_setup("hermes")
    first["channels"][0]["automatic_reply"] = "connected"
    first["sources"].clear()
    fresh = build_native_agent_setup("hermes")
    assert fresh["channels"][0]["automatic_reply"] is True
    assert fresh["sources"]


@pytest.mark.parametrize("agent", ["hermes", "openclaw"])
@pytest.mark.parametrize("voice", [item["id"] for item in available_voices()])
def test_native_setup_preserves_every_available_voice(agent, voice):
    setup = build_native_agent_setup(agent, voice)
    tts = setup["config"]["tts"]
    provider = tts["openai"] if agent == "hermes" else tts["providers"]["openai"]
    assert setup["voice"] == provider["voice"] == voice


@pytest.mark.parametrize("agent", ["hermes", "openclaw"])
def test_new_native_setup_defaults_to_english_voice(agent):
    assert build_native_agent_setup(agent)["voice"] == "sophie"
