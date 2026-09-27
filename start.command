#!/bin/zsh
set -eu
cd "$(dirname "$0")"
export LOUDTALK_DATA_DIR="$PWD/.loudtalk"
export HF_HOME="$LOUDTALK_DATA_DIR/models"
if ! command -v uv >/dev/null; then
  export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
fi
if ! command -v uv >/dev/null; then
  echo 'Install uv: brew install uv'
  exit 1
fi
python3 -c 'import shutil,sys; free=shutil.disk_usage(".").free/1024**3; print(f"{free:.1f} GB free"); sys.exit(0 if free >= 20 else "At least 20 GB of free disk space is required.")'
if ! command -v ffmpeg >/dev/null; then
  echo 'Install FFmpeg: brew install ffmpeg'
  exit 1
fi
uv sync --locked --extra discord
exec .venv/bin/loudtalk serve --open
