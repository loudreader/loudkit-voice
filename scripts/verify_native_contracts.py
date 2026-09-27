#!/usr/bin/env python3
"""Execute pinned Hermes audio functions through the real SDK against LoudTalk.

Run with the development environment plus OpenAI's SDK, for example:
  uv run --with openai python scripts/verify_native_contracts.py --base-url http://127.0.0.1:8765/v1

This is a provider integration test, not a Hermes gateway or messenger test.
Only selected upstream functions execute; profile access and unrelated provider
dependencies are supplied locally. No messaging token, account or API is used.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from loudtalk.native_agents import _validate_base_url, build_native_agent_setup

COMMIT = "969872ebaa1453f877523adc8aa8c6f69b24cbe3"
SOURCE_FILES = {
    "transcription_cloud.py": "956360841aed2e8723c20fbcfa8676feaa7d813ac32fb4b74688c3f87f256992",
    "tts_tool_openai.py": "9a2dfa0c5f7564e7ec796636ed0fce69896bbc9a7fbf14c0081267a880206874",
    "tts_tool_providers.py": "b01b253e4887aa16d063b684d2cb3c7caf2b6655d69e9187c9c52d448ca7c740",
}


def load_source(name: str, root: Path) -> Path:
    path = root / name
    if not path.exists():
        url = f"https://raw.githubusercontent.com/NousResearch/hermes-agent/{COMMIT}/tools/{name}"
        with urlopen(Request(url, headers={"User-Agent": "LoudTalk compatibility test"}),
                     timeout=30) as response:
            path.write_bytes(response.read())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = SOURCE_FILES[name]
    if expected and digest != expected:
        raise ValueError(f"Upstream source digest mismatch: {name}")
    return path


def extract(path: Path, functions: set[str], constants: set[str], namespace: dict) -> dict:
    selected = []
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in constants for target in node.targets
        ):
            selected.append(node)
    found = {node.name for node in selected if isinstance(node, ast.FunctionDef)}
    if found != functions:
        raise ValueError(f"Pinned upstream functions missing: {functions - found}")
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.Module(body=[future, *selected], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace


def module(name, **values):
    result = ModuleType(name)
    result.__dict__.update(values)
    return result


def forbidden(*args, **kwargs):
    raise AssertionError("Test tried an unrelated credential, managed gateway, or transcode fallback")


def probe(path: Path) -> dict:
    process = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate:format=duration",
         "-of", "json", str(path)], check=True, capture_output=True, text=True, timeout=30,
    )
    result = json.loads(process.stdout)
    assert float(result["format"]["duration"]) > 0
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765/v1")
    parser.add_argument("--source-dir", type=Path, default=Path(tempfile.gettempdir()) / "loudtalk-hermes-source")
    parser.add_argument("--output-dir", type=Path, default=Path(tempfile.gettempdir()) / "loudtalk-native-live")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--text", default="Jutro rano przypomnij mi o spotkaniu z zespołem.")
    args = parser.parse_args()
    base_url = _validate_base_url(args.base_url)
    args.source_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        import openai
    except ImportError:
        parser.error("OpenAI SDK missing. Run with uv run --with openai python ...")

    sources = {name: load_source(name, args.source_dir) for name in SOURCE_FILES}
    config = build_native_agent_setup("hermes", base_url=base_url)["config"]
    def section(values, key):
        return values.get(key) or {}  # Config seam, not an audio implementation.

    def read_selection(key):
        return config[key]["provider"]
    common = {
        "logger": logging.getLogger("hermes-live"), "urljoin": urljoin, "Path": Path,
        "tempfile": tempfile, "re": re, "uuid": uuid,
    }
    helpers = extract(sources["tts_tool_providers.py"], {"_tts_response_format_from_path"}, set(), {})
    stt = extract(sources["transcription_cloud.py"], {
        "_with_openai_client", "_transcribe_openai", "_is_local_or_private_url",
        "_direct_openai_credentials", "_resolve_openai_audio_client_config", "_extract_transcript_text",
    }, {"_ASR_TEXT_RE"}, {
        **common, "_get_stt_section": section,
        "_error_result": lambda error, **extra: {"success": False, "error": error, **extra},
        "_ok_result": lambda text, provider: {"success": True, "transcript": text, "provider": provider},
        "_transcode_audio_for_stt": forbidden, "OPENAI_BASE_URL": "https://api.openai.com/v1",
        "GROQ_MODELS": set(), "DEFAULT_STT_MODEL": "whisper-1",
    })
    tts = extract(sources["tts_tool_openai.py"], {
        "_managed_openai_audio_route", "_resolve_openai_audio_client_config", "_openai_extra_body",
        "_generate_openai_tts",
    }, {"DEFAULT_OPENAI_MODEL", "DEFAULT_OPENAI_VOICE", "DEFAULT_OPENAI_BASE_URL", "MANAGED_OPENAI_TTS_MODELS"}, {
        **common, "_section": section,
        "_origin": lambda: SimpleNamespace(_load_tts_config=lambda: config["tts"],
                                            _import_openai_client=lambda: openai.OpenAI),
        "_tts_response_format_from_path": helpers["_tts_response_format_from_path"],
        "NOUS_MANAGED_PROVIDER": "nous", "read_selection": read_selection,
        "resolve_managed_tool_gateway": forbidden, "resolve_openai_audio_api_key": forbidden,
        "selection_error": lambda *values: repr(values), "managed_nous_tools_enabled": lambda: False,
        "nous_tool_gateway_unavailable_message": forbidden,
    })
    stubs = {
        "tools": module("tools"),
        "tools.transcription_tools": module("tools.transcription_tools",
            _load_stt_config=lambda: config["stt"], _HAS_OPENAI=True,
            _resolve_stt_language=lambda provider: None),
        "tools.transcription_common": module("tools.transcription_common", DEFAULT_STT_TIMEOUT=300,
            _config_number=lambda cfg, key, default, cast=float: cast(cfg.get(key, default))),
        "tools.managed_tool_gateway": module("tools.managed_tool_gateway", resolve_managed_tool_gateway=forbidden),
        "tools.tool_backend_helpers": module("tools.tool_backend_helpers", NOUS_MANAGED_PROVIDER="nous",
            resolve_openai_audio_api_key=forbidden, read_selection=read_selection,
            selection_error=lambda *values: repr(values), managed_nous_tools_enabled=lambda: False,
            nous_tool_gateway_unavailable_message=forbidden),
    }
    results = []
    with patch.dict(sys.modules, stubs):
        assert stt["_resolve_openai_audio_client_config"]() == ("loudtalk-local", base_url)
        assert tts["_resolve_openai_audio_client_config"]() == ("loudtalk-local", base_url, False)
        for extension, codec in [("ogg", "opus"), ("mp3", "mp3")]:
            output = args.output_dir / f"hermes-reply.{extension}"
            tts["_generate_openai_tts"](args.text, str(output), config["tts"])
            audio = probe(output)
            assert audio["streams"][0]["codec_name"] == codec, audio
            results.append({"kind": "tts", "response_format": codec, "audio": audio,
                            "output_bytes": output.stat().st_size})
        transcription = stt["_transcribe_openai"](str(args.output_dir / "hermes-reply.ogg"), "parakeet")
        assert transcription["success"], transcription
        text = transcription["transcript"].strip()
        transcript_match = re.findall(r"\w+", args.text.casefold()) == re.findall(r"\w+", text.casefold())
        results.append({"kind": "stt", "input_format": "ogg/opus", "model": "parakeet", "text": text})
    evidence = {
        "passed": transcript_match, "transport_passed": True,
        "transcript_matches_spoken_text": transcript_match,
        "spoken_text": args.text,
        "checked_at": datetime.now(UTC).isoformat(), "agent": "hermes",
        "scope": "Pinned upstream provider functions + real OpenAI SDK + local HTTP + real speech codecs/transcription",
        "not_tested": ["Hermes gateway startup", "profile file loading", "messenger delivery"],
        "dependency_seams": ["profile/config helpers", "unused managed/cloud providers", "STT result envelope"],
        "base_url": base_url, "openai_sdk": openai.__version__, "source_commit": COMMIT,
        "sources": {name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "url": f"https://github.com/NousResearch/hermes-agent/blob/{COMMIT}/tools/{name}"}
                    for name, path in sources.items()},
        "results": results,
    }
    serialized = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(serialized)
    print(serialized)
    if not transcript_match:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
