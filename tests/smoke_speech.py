"""Opt-in real audio smoke: Loudkit → browser WebM → Parakeet, Polish + English.

Run from the project directory after installing speech dependencies:
    .venv/bin/python tests/smoke_speech.py --data-dir /tmp/loudtalk-speech-smoke

This really loads/downloads both models; pytest never collects it. A passing
result includes non-silent WAVs, meaningful ASR agreement and input rejection.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from loudtalk.speech import SpeechService


def run(data_dir: Path) -> None:
    speech = SpeechService(data_dir)
    start = time.monotonic()
    print("Loading real Loudkit and Parakeet models…", flush=True)
    speech.prepare()
    assert speech.status()["tts"]["state"] == "ready", speech.status()
    assert speech.status()["stt"]["state"] == "ready", speech.status()
    assert not speech.status()["preparing"]
    report = {"prepare_seconds": round(time.monotonic() - start, 2), "clips": []}
    for voice, text, expected in (
        (
            "gosia",
            "Cześć, to jest wiadomość głosowa. Powiedz mi, jaki mamy plan na jutro.",
            {"jest", "wiadomość", "głosowa", "plan", "jutro"},
        ),
        (
            "joe",
            "Hello, this is a voice message. Please tell me the plan for tomorrow.",
            {"voice", "message", "tell", "plan", "tomorrow"},
        ),
    ):
        start = time.monotonic()
        generated = speech.synthesize(text, voice)
        wav = speech.resolve_audio(generated["audio_id"])
        samples, rate = sf.read(wav)
        assert rate >= 16000 and len(samples) > rate, (rate, samples.shape)
        rms = float(np.sqrt(np.mean(np.square(samples))))
        assert np.isfinite(samples).all() and rms > 0.001, rms
        tts_seconds = time.monotonic() - start
        # Exercise the same Opus/WebM recording container as Chrome, not just
        # WAV round-tripping. Retain these explicit smoke artifacts for review.
        webm = data_dir / f"{voice}-browser.webm"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(wav),
                "-c:a",
                "libopus",
                str(webm),
            ],
            check=True,
        )
        start = time.monotonic()
        recognized = speech.transcribe(webm)
        words = set(re.findall(r"\w+", recognized["text"].lower()))
        overlap = expected & words
        assert len(overlap) >= 4, (voice, recognized["text"], expected - words)
        normalized, normalized_rate = sf.read(speech.resolve_audio(recognized["audio_id"]))
        assert normalized.ndim == 1 and normalized_rate == 16000
        clip = {
            "voice": voice,
            "input": text,
            "transcript": recognized["text"],
            "duration": generated["duration"],
            "rms": rms,
            "tts_seconds": round(tts_seconds, 2),
            "stt_seconds": round(time.monotonic() - start, 2),
            "audio": str(wav),
            "browser_audio": str(webm),
        }
        report["clips"].append(clip)
        print(json.dumps(clip, ensure_ascii=False), flush=True)

    rejected = []
    for label, action in (
        ("path traversal", lambda: speech.resolve_audio("../../etc/passwd")),
        ("empty text", lambda: speech.synthesize("  ", "gosia")),
        ("unknown voice", lambda: speech.synthesize("Hello", "not-a-voice")),
    ):
        try:
            action()
        except ValueError:
            rejected.append(label)
        else:
            raise AssertionError(f"Accepted invalid input: {label}")

    # A highly compressed long file can be tiny; enforce decoded duration too.
    overlong = data_dir / "overlong.wav"
    sf.write(overlong, np.zeros(181 * 16000, dtype=np.float32), 16000)
    before = set(speech.audio_dir.iterdir())
    try:
        speech.transcribe(overlong)
    except ValueError as exc:
        assert "3 minuty" in str(exc), str(exc)
        rejected.append("audio > 180 seconds")
    else:
        raise AssertionError("Accepted overlong audio")
    finally:
        overlong.unlink()
    assert set(speech.audio_dir.iterdir()) == before, "Invalid audio leaked into storage"
    report["rejected"] = rejected
    report["status"] = speech.status()
    destination = data_dir / "smoke-report.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"PASS — {destination}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("/tmp/loudtalk-speech-smoke"))
    run(parser.parse_args().data_dir)
