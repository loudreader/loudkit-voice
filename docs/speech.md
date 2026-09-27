# Local speech

Loudtalk uses the public Loudkit Python package (pinned in `uv.lock`) for text to speech, and
Parakeet TDT 0.6B v3 through `parakeet-mlx` for recognition. The speech server
currently requires macOS on Apple Silicon; agents connecting to its API can
run anywhere. FFmpeg converts browser WebM/Opus, Safari M4A and imported audio
to 16 kHz mono PCM WAV before transcription. Input is limited to 25 MB and
three minutes, including compressed files with misleading metadata.

The models load once, on preparation or first use. Download/load failures stay
visible in status and propagate to the caller. There is no placeholder audio
or fake transcript. At least 20 GiB of free disk is required before model
loading and audio operations. Model downloads are retained in the standard
Hugging Face cache.

Configuration:

| Variable | Default |
| --- | --- |
| `LOUDTALK_TTS_MODEL` | `loudreader/loudr-1-turbo` (or a local Loudkit release path) |
| `LOUDTALK_TTS_DEVICE` | Loudkit automatic device selection; `mps` on Apple Silicon |
| `LOUDTALK_STT_MODEL` | `mlx-community/parakeet-tdt-0.6b-v3` (or a local MLX model directory) |

Both Polish voices, Gosia and Darkman, and the other 26 voices in Loudkit's
release are available. Voice language determines synthesis language. Parakeet
v3 detects speech language automatically, including Polish.

The opt-in smoke test performs actual Polish and English synthesis, encodes
the generated speech as browser WebM, transcribes it with Parakeet, checks
recognition against expected words, and verifies canonical audio and rejected
inputs:

```sh
.venv/bin/python tests/smoke_speech.py --data-dir /tmp/loudtalk-speech-smoke
```

Review the printed WAV paths and `smoke-report.json`. This smoke test is not a
microphone or listening quality evaluation; those still need a person and a
working microphone/speaker. Normal `pytest` does not download speech models.

Upstream API references: [Parakeet MLX](https://github.com/senstella/parakeet-mlx),
[NVIDIA's Parakeet v3 model card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3),
and [Loudkit's voice roster](https://github.com/loudreader/loudkit/blob/main/VOICES.md).
