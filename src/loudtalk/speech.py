"""Real local speech: Loudkit synthesis and Parakeet recognition on Apple Silicon.

Models load lazily and stay resident. Nothing is downloaded by importing this
module or asking for status. Callers should run these blocking methods in a
worker thread; a shared lock serializes model access across all callers.
"""

from __future__ import annotations

import math
import os
import platform
import re
import shutil
import subprocess
import threading
import wave
from pathlib import Path
from typing import Any
from uuid import uuid4

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_AUDIO_SECONDS = 180.0
MAX_TEXT_CHARACTERS = 12000
MIN_FREE_BYTES = 20 * 1024**3
TTS_MODEL = "loudreader/loudr-1-turbo"
STT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"

# The release roster in loudkit/VOICES.md, including both Polish voices.
_VOICE_LANGUAGES = (
    ("gosia", "pl"),
    ("darkman", "pl"),
    ("clara", "en"),
    ("emma", "en"),
    ("henry", "en"),
    ("joe", "en"),
    ("kathleen", "en"),
    ("lucy", "en"),
    ("miles", "en"),
    ("oliver", "en"),
    ("oscar", "en"),
    ("sophie", "en"),
    ("carmen", "es"),
    ("dave", "es"),
    ("colette", "fr"),
    ("henri", "fr"),
    ("kerstin", "de"),
    ("thorsten", "de"),
    ("dante", "it"),
    ("paola", "it"),
    ("tugao", "pt"),
    ("ines", "pt"),
    ("nathalie", "nl"),
    ("pim", "nl"),
    ("nils", "sv"),
    ("selma", "sv"),
    ("freja", "da"),
    ("soren", "da"),
)
_VOICE_IDS = frozenset(name for name, _ in _VOICE_LANGUAGES)


class SpeechError(RuntimeError):
    """A speech dependency or engine failed; there is no simulated fallback."""


def available_voices() -> list[dict[str, str]]:
    """Return the actual bundled voice roster without loading any models."""
    return [
        {"id": name, "name": name.title(), "language": language}
        for name, language in _VOICE_LANGUAGES
    ]


class SpeechService:
    def __init__(self, data_dir: Path):
        self.audio_dir = Path(data_dir).expanduser().resolve() / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.tts_model = os.environ.get("LOUDTALK_TTS_MODEL", TTS_MODEL)
        self.stt_model = os.environ.get("LOUDTALK_STT_MODEL", STT_MODEL)
        self._engine_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._tts: Any = None
        self._stt: Any = None
        self._voice_profiles: dict[str, Any] = {}
        self._states: dict[str, dict[str, str | None]] = {
            "tts": {"state": "idle", "error": None},
            "stt": {"state": "idle", "error": None},
        }
        self._preparing = False

    @staticmethod
    def available_voices() -> list[dict[str, str]]:
        return available_voices()

    def status(self) -> dict[str, Any]:
        """Report actual resident model state without blocking on model loading."""
        with self._state_lock:
            return {
                "tts": dict(self._states["tts"]),
                "stt": dict(self._states["stt"]),
                "preparing": self._preparing,
            }

    def _set_state(self, engine: str, state: str, error: str | None = None) -> None:
        with self._state_lock:
            self._states[engine] = {"state": state, "error": error}

    def _check_disk(self) -> None:
        # Check both destinations: HF_CACHE and user audio may be on different
        # volumes. Keep the user's 20 GiB reserve before any model download.
        cache_base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        hf_home = Path(os.environ.get("HF_HOME", str(cache_base / "huggingface")))
        hub_cache = os.environ.get(
            "HF_HUB_CACHE", os.environ.get("HUGGINGFACE_HUB_CACHE", str(hf_home / "hub"))
        )
        xet_cache = os.environ.get("HF_XET_CACHE", str(hf_home / "xet"))
        paths = [
            Path("/"),
            self.audio_dir,
            *(Path(os.path.expandvars(value)).expanduser() for value in (hub_cache, xet_cache)),
        ]
        for path in paths:
            while not path.exists() and path != path.parent:
                path = path.parent
            free = shutil.disk_usage(path).free
            if free < MIN_FREE_BYTES:
                raise SpeechError(
                    f"Za mało miejsca na dysku: {free / 1024**3:.1f} GB wolne. "
                    "Potrzeba co najmniej 20 GB wolnego miejsca."
                )

    def _ensure_tts(self) -> None:
        if self._tts is not None and not self._tts.wedged:
            self._set_state("tts", "ready")
            return
        self._tts = None
        self._voice_profiles.clear()
        self._set_state("tts", "loading")
        try:
            self._check_disk()
            import loudkit

            self._tts = loudkit.load(
                self.tts_model, device=os.environ.get("LOUDTALK_TTS_DEVICE") or None
            )
            self._set_state("tts", "ready")
        except Exception as exc:
            self._tts = None
            message = f"Loudkit nie jest gotowy: {exc}"
            self._set_state("tts", "error", message)
            raise SpeechError(message) from exc

    def _ensure_stt(self) -> None:
        if self._stt is not None:
            self._set_state("stt", "ready")
            return
        self._set_state("stt", "loading")
        try:
            if platform.system() != "Darwin" or platform.machine() != "arm64":
                raise SpeechError(
                    "Ta wersja rozpoznawania mowy wymaga Maca z Apple Silicon. "
                    "Agent może działać na dowolnym komputerze i łączyć się przez API."
                )
            if not shutil.which("ffmpeg"):
                raise SpeechError("Brakuje FFmpeg. Zainstaluj go poleceniem: brew install ffmpeg")
            self._check_disk()
            import mlx.core as mx
            from parakeet_mlx import from_pretrained

            model = from_pretrained(self.stt_model)
            # MLX is lazy: materialize all parameters before saying ready.
            mx.eval(model.parameters())
            self._stt = model
            self._set_state("stt", "ready")
        except Exception as exc:
            self._stt = None
            message = f"Parakeet nie jest gotowy: {exc}"
            self._set_state("stt", "error", message)
            raise SpeechError(message) from exc

    def prepare(self) -> None:
        """Synchronously load both engines; failures remain visible in status."""
        with self._engine_lock:
            with self._state_lock:
                self._preparing = True
            errors = []
            try:
                for load in (self._ensure_tts, self._ensure_stt):
                    try:
                        load()
                    except SpeechError as exc:
                        errors.append(str(exc))
                if errors:
                    raise SpeechError("\n".join(errors))
            finally:
                with self._state_lock:
                    self._preparing = False

    def resolve_audio(self, audio_id: str) -> Path:
        """Resolve only an existing audio UUID; paths and extensions are refused."""
        if not isinstance(audio_id, str) or not re.fullmatch(r"[0-9a-f]{32}", audio_id):
            raise ValueError("Nieprawidłowy identyfikator nagrania.")
        path = self.audio_dir / f"{audio_id}.wav"
        if path.is_symlink() or path.resolve().parent != self.audio_dir:
            raise ValueError("Nieprawidłowa ścieżka nagrania.")
        if not path.is_file():
            raise FileNotFoundError("Nie znaleziono nagrania.")
        return path

    def synthesize(self, text: str, voice: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Wpisz tekst do przeczytania.")
        if len(text) > MAX_TEXT_CHARACTERS:
            raise ValueError(f"Tekst może mieć maksymalnie {MAX_TEXT_CHARACTERS} znaków.")
        if voice not in _VOICE_IDS:
            raise ValueError("Wybierz jeden z dostępnych głosów Loudkit.")
        with self._engine_lock:
            self._check_disk()
            self._ensure_tts()
            audio_id = uuid4().hex
            path = self.audio_dir / f"{audio_id}.wav"
            partial = self.audio_dir / f".{audio_id}.partial.wav"
            try:
                if voice not in self._voice_profiles:
                    import loudkit

                    # New HF Hub caches share blobs across model repositories.
                    # Loudkit's name lookup currently rejects those legitimate
                    # symlinks. Its supported explicit-file API reads the same
                    # voice from the release already verified by loudkit.load.
                    voice_path = (
                        Path(self._tts.checkpoint_path).parent / "voices" / f"{voice}.safetensors"
                    )
                    self._voice_profiles[voice] = loudkit.voice(str(voice_path))
                result = self._tts.synthesize(text.strip(), self._voice_profiles[voice])
                duration = float(result.duration)
                if not math.isfinite(duration) or duration <= 0:
                    raise SpeechError("Loudkit nie wygenerował poprawnego nagrania.")
                result.save(str(partial), include_provenance=False)
                partial.replace(path)
                self._set_state("tts", "ready")
                return {
                    "audio_id": audio_id,
                    "audio_url": f"/audio/{audio_id}.wav",
                    "duration": duration,
                }
            except Exception as exc:
                partial.unlink(missing_ok=True)
                path.unlink(missing_ok=True)
                message = f"Nie udało się wygenerować głosówki: {exc}"
                self._set_state("tts", "error", message)
                raise SpeechError(message) from exc

    def _normalize(self, source: Path, target: Path) -> float:
        if not source.is_file():
            raise ValueError("Nie znaleziono pliku audio.")
        size = source.stat().st_size
        if not size:
            raise ValueError("Plik audio jest pusty.")
        if size > MAX_AUDIO_BYTES:
            raise ValueError("Nagranie może mieć maksymalnie 25 MB.")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise SpeechError("Brakuje FFmpeg. Zainstaluj go poleceniem: brew install ffmpeg")
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-protocol_whitelist",
                    "file,pipe",
                    # Refuse playlists and demuxers capable of following local
                    # references; recording formats themselves stay supported.
                    "-format_whitelist",
                    "wav,mp3,mov,matroska,webm,ogg,flac,aac",
                    "-i",
                    str(source.resolve()),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    # Decode slightly past the limit, then refuse overlong audio.
                    # This bounds decompression even for malicious input headers.
                    "-t",
                    str(MAX_AUDIO_SECONDS + 0.1),
                    "-c:a",
                    "pcm_s16le",
                    str(target),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            with wave.open(str(target), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
            if duration > MAX_AUDIO_SECONDS:
                raise ValueError("Nagranie może trwać maksymalnie 3 minuty.")
            if duration < 0.1:
                raise ValueError("Nagranie jest za krótkie. Nagraj co najmniej chwilę mowy.")
            return duration
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Odczyt nagrania trwał zbyt długo. Spróbuj krótszego pliku.") from exc
        except (subprocess.CalledProcessError, wave.Error, EOFError) as exc:
            raise ValueError("Nie można odczytać audio. Użyj WAV, MP3, M4A, OGG lub WebM.") from exc

    def transcribe(self, path: Path) -> dict[str, Any]:
        with self._engine_lock:
            self._check_disk()
            audio_id = uuid4().hex
            canonical = self.audio_dir / f"{audio_id}.wav"
            try:
                duration = self._normalize(Path(path), canonical)
                self._ensure_stt()
                try:
                    result = self._stt.transcribe(
                        str(canonical), chunk_duration=60.0, overlap_duration=10.0
                    )
                    text = result.text.strip()
                except Exception as exc:
                    message = f"Nie udało się rozpoznać mowy: {exc}"
                    self._set_state("stt", "error", message)
                    raise SpeechError(message) from exc
                if not text:
                    raise ValueError("Nie rozpoznano mowy. Spróbuj nagrać wiadomość ponownie.")
                self._set_state("stt", "ready")
                return {
                    "text": text,
                    "audio_id": audio_id,
                    "audio_url": f"/audio/{audio_id}.wav",
                    "duration": duration,
                }
            except Exception:
                canonical.unlink(missing_ok=True)
                raise
