"""Decode the actual OpenAI-compatible audio exports without loading TTS models."""

from __future__ import annotations

import io
import json
import math
import shutil
import struct
import subprocess
import wave
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from loudtalk.channels import base
from loudtalk.server import create_app
from loudtalk.speech import SpeechService


class ToneSpeech(SpeechService):
    """Two seconds of a tone lets FFmpeg's tempo filter settle."""

    def __init__(self, data_dir):
        super().__init__(data_dir)
        self.calls = []

    def synthesize(self, text, voice):
        self.calls.append((text, voice))
        audio_id = uuid4().hex
        output = self.audio_dir / f"{audio_id}.wav"
        with wave.open(str(output), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(
                b"".join(
                    struct.pack("<h", round(2000 * math.sin(2 * math.pi * 440 * n / 16000)))
                    for n in range(32000)
                )
            )
        return {"audio_id": audio_id, "audio_url": f"/audio/{audio_id}.wav", "duration": 2.0}


@pytest.fixture
def voice_api(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Real audio export tests require FFmpeg and ffprobe.")
    speech = ToneSpeech(tmp_path)
    with TestClient(create_app(tmp_path, speech), raise_server_exceptions=False) as client:
        yield client, speech


@pytest.mark.parametrize(
    ("format_name", "extension", "mime", "codec", "sample_rate"),
    [
        (None, "mp3", "audio/mpeg", "mp3", 16000),
        ("mp3", "mp3", "audio/mpeg", "mp3", 16000),
        ("opus", "ogg", "audio/ogg", "opus", 48000),
        ("aac", "aac", "audio/aac", "aac", 16000),
        ("flac", "flac", "audio/flac", "flac", 16000),
        ("wav", "wav", "audio/wav", "pcm_s16le", 16000),
        ("pcm", "pcm", "audio/pcm", "pcm_s16le", 24000),
    ],
)
def test_compatible_audio_decodes_and_exports_are_removed(
    voice_api, tmp_path, format_name, extension, mime, codec, sample_rate
):
    client, speech = voice_api
    body = {"input": "An explicit test tone.", "voice": "gosia", "model": "tts-1"}
    if format_name is not None:
        body["response_format"] = format_name
    response = client.post("/v1/audio/speech", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == mime
    assert f"speech.{extension}" in response.headers["content-disposition"]
    audio_path = tmp_path / f"received.{extension}"
    audio_path.write_bytes(response.content)
    input_options = ["-f", "s16le", "-ar", "24000"] if format_name == "pcm" else []
    probe = subprocess.run(
        ["ffprobe", "-v", "error", *input_options, "-show_streams", "-of", "json", str(audio_path)],
        capture_output=True,
        check=True,
        timeout=20,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert len(streams) == 1
    assert streams[0]["codec_name"] == codec
    assert int(streams[0]["sample_rate"]) == sample_rate
    assert streams[0]["channels"] == 1
    # Probe metadata alone would miss corrupt packets: decode the complete response too.
    subprocess.run(
        ["ffmpeg", "-v", "error", *input_options, "-i", str(audio_path), "-f", "null", "-"],
        capture_output=True,
        check=True,
        timeout=20,
    )
    assert list(speech.audio_dir.glob(".export-*")) == []
    originals = list(speech.audio_dir.glob("*.wav"))
    assert len(originals) == 1
    with wave.open(str(originals[0]), "rb") as audio:
        assert audio.getnframes() / audio.getframerate() == 2.0
    assert speech.calls == [("An explicit test tone.", "gosia")]


@pytest.mark.parametrize("speed", [0.5, 2.0])
def test_speed_changes_actual_audio_duration(voice_api, speed):
    client, speech = voice_api
    response = client.post(
        "/v1/audio/speech",
        json={"input": "Tempo test.", "response_format": "wav", "speed": speed},
    )
    assert response.status_code == 200, response.text
    with wave.open(io.BytesIO(response.content), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    assert duration == pytest.approx(2.0 / speed, abs=0.15)
    assert not list(speech.audio_dir.glob(".export-*"))


@pytest.mark.parametrize(
    "options",
    [
        {"response_format": "ogg"},
        {"response_format": "m4a"},
        {"speed": 0.49},
        {"speed": 2.01},
    ],
)
def test_unsupported_audio_options_fail_before_synthesis(voice_api, options):
    client, speech = voice_api
    response = client.post("/v1/audio/speech", json={"input": "Do not synthesize.", **options})
    assert response.status_code == 422
    assert speech.calls == []


def test_missing_encoder_reports_an_actionable_error_without_deleting_original(
    voice_api, monkeypatch
):
    client, speech = voice_api
    monkeypatch.setattr(base.shutil, "which", lambda executable: None)
    response = client.post("/v1/audio/speech", json={"input": "Encoder unavailable."})
    assert response.status_code == 503
    assert "FFmpeg" in response.json()["detail"]
    assert not list(speech.audio_dir.glob(".export-*"))
    assert len(list(speech.audio_dir.glob("*.wav"))) == 1
