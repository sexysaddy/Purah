#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  printf 'PURAH: не найден %s\nСначала создай venv и установи базовые requirements.txt.\n' "$PYTHON" >&2
  exit 1
fi
"$PYTHON" "$ROOT/ensure_config.py"
command -v curl >/dev/null || { echo 'Нужен curl (Arch: sudo pacman -S curl)' >&2; exit 1; }
command -v unzip >/dev/null || { echo 'Нужен unzip (Arch: sudo pacman -S unzip)' >&2; exit 1; }
"$PYTHON" -m pip install -r "$ROOT/requirements.txt" -r "$ROOT/requirements-wakeword.txt"
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/purah"
MODEL_NAME='vosk-model-small-ru-0.22'
MODEL_DIR="$DATA_ROOT/$MODEL_NAME"
if [[ -d "$MODEL_DIR" && -f "$MODEL_DIR/am/final.mdl" ]]; then
  echo "Wake-word model already installed: $MODEL_DIR"
  exit 0
fi
mkdir -p "$DATA_ROOT"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
printf 'Downloading Russian offline wake model (~45 MB)…\n'
curl --fail --location --retry 3 --connect-timeout 20 \
  'https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip' \
  --output "$TMP_DIR/model.zip"
unzip -q "$TMP_DIR/model.zip" -d "$TMP_DIR/extracted"
test -f "$TMP_DIR/extracted/$MODEL_NAME/am/final.mdl"
rm -rf "$MODEL_DIR"
mv "$TMP_DIR/extracted/$MODEL_NAME" "$MODEL_DIR"
echo "Installed Vosk model: $MODEL_DIR"
echo 'Теперь запускай ./start-purah.sh; фраза пробуждения: «Привет, Пура».'
