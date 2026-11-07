#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  printf 'PURAH: не найден %s\nСначала создай venv и установи requirements.txt.\n' "$PYTHON" >&2
  exit 1
fi
command -v curl >/dev/null || { echo 'Нужен curl (Arch: sudo pacman -S curl)' >&2; exit 1; }
"$PYTHON" "$ROOT/ensure_config.py"
"$PYTHON" -m pip install --upgrade piper-tts
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/purah/piper"
MODEL="$DATA_ROOT/ru_RU-irina-medium.onnx"
CONFIG="$MODEL.json"
mkdir -p "$DATA_ROOT"
if [[ -s "$MODEL" ]]; then
  echo '8ff38212d23da300bbe3705c645e6e5b9475f0bfde01558eb17813e22acaaaaa  '"$MODEL" | sha256sum --check --status || {
    echo 'Existing Piper model checksum mismatch; re-downloading.' >&2
    rm -f "$MODEL"
  }
fi
if [[ ! -s "$MODEL" ]]; then
  echo 'Downloading Piper Russian female voice Irina (~63 MB)…'
  curl --fail --location --retry 3 --connect-timeout 20 \
    'https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx' \
    --output "$MODEL.tmp"
  echo '8ff38212d23da300bbe3705c645e6e5b9475f0bfde01558eb17813e22acaaaaa  '"$MODEL.tmp" | sha256sum --check --status || {
    rm -f "$MODEL.tmp"
    echo 'Piper model checksum mismatch; download discarded.' >&2
    exit 1
  }
  mv "$MODEL.tmp" "$MODEL"
else
  echo "Piper model already present: $MODEL"
fi
if [[ ! -s "$CONFIG" ]]; then
  curl --fail --location --retry 3 --connect-timeout 20 \
    'https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json' \
    --output "$CONFIG.tmp"
  "$PYTHON" -m json.tool "$CONFIG.tmp" >/dev/null
  mv "$CONFIG.tmp" "$CONFIG"
fi
"$PYTHON" "$ROOT/configure_tts.py" --engine piper --piper-model "$MODEL" --warmup-on-start false
printf '\nPiper установлен и выбран в config.toml. Голос берётся из ONNX-модели Irina.\n'
