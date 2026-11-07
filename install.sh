#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

# Optional connectors are opt-in. A diagnostic check never invokes sudo, pip
# or any network service. Unknown flags fail rather than silently doing an
# unexpected full installation.
WITH_TELEGRAM=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --with-telegram) WITH_TELEGRAM=1 ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help)
      cat <<'HELP'
Usage: ./install.sh [--with-telegram] [--check]
  (no flags)       Install core PURAH, AT-SPI, Firefox/Chromium client library,
                   CLI adapters, voice wake model and chosen TTS engine.
  --with-telegram  Also install optional Telethon packages. To authorize the
                   account, run ./.venv/bin/python setup_telegram.py afterward.
  --check          Read-only dependency/config check; nonzero on missing core.
  --help           Show this message.
HELP
      exit 0 ;;
    *) echo "Неизвестный аргумент установщика: $arg" >&2; exit 2 ;;
  esac
done

if (( CHECK_ONLY )); then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    exec "$ROOT/.venv/bin/python" "$ROOT/doctor.py" --strict
  fi
  exec "$PYTHON_BIN" "$ROOT/doctor.py" --strict
fi

printf '\n╭──────────────────────────────────────────────╮\n'
printf '│              PURAH installer                │\n'
printf '╰──────────────────────────────────────────────╯\n\n'
printf 'Каталог: %s\n' "$ROOT"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Не найден $PYTHON_BIN. Установи Python 3.11+ и повтори запуск." >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Для PURAH требуется Python 3.11+; текущий: $("$PYTHON_BIN" --version 2>&1)" >&2
  exit 1
fi

# PURAH is developed/tested for Arch-family systems with Hyprland/Quickshell.
if command -v pacman >/dev/null 2>&1; then
  if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=()
  else
    command -v sudo >/dev/null 2>&1 || { echo 'Нужен sudo для установки системных пакетов.' >&2; exit 1; }
    sudo -v
    SUDO=(sudo)
  fi
  echo '1/5 · Устанавливаю системные зависимости через pacman…'
  "${SUDO[@]}" pacman -S --needed python python-pip portaudio xorg-xwayland quickshell grim espeak-ng curl unzip wtype ydotool tesseract tesseract-data-rus tesseract-data-eng tmux at-spi2-core python-gobject gobject-introspection ripgrep glib2
  # Mouse automation needs ydotoold; the Arch package ships a user service,
  # but installing the package does not start it. Try enabling it in this session
  # without making an inaccessible /dev/uinput fatal to the whole installer.
  if command -v systemctl >/dev/null 2>&1 && [[ -n "${XDG_RUNTIME_DIR:-}" ]] && \
     systemctl --user cat ydotool.service >/dev/null 2>&1; then
    echo 'Запускаю пользовательский сервис ydotool для управления мышью…'
    if systemctl --user enable --now ydotool.service; then
      echo 'ydotool.service запущен и добавлен в пользовательский автозапуск.'
    else
      echo 'Предупреждение: ydotool.service не запустился. Установка продолжится; см. README_RU.md (права /dev/uinput).'
    fi
  else
    echo 'Предупреждение: не обнаружен доступный пользовательский ydotool.service. При необходимости запусти: systemctl --user enable --now ydotool.service'
  fi
else
  cat >&2 <<'EOF'
Автоматическая установка системных пакетов сейчас поддерживается для Arch Linux и производных.
Для другого дистрибутива заранее установи Python 3.11+, pip/venv, PortAudio, curl, unzip,
eSpeak NG, Quickshell и grim, затем повтори ./install.sh.
EOF
  for command_name in curl unzip espeak-ng qs hyprctl Xwayland grim wtype tesseract tmux rg gdbus; do
    command -v "$command_name" >/dev/null 2>&1 || {
      echo "Не найдена системная зависимость '$command_name'. Установи её вручную и повтори запуск." >&2
      exit 1
    }
  done
fi

if [[ ! -d "$ROOT/.venv" ]]; then
  echo 'Создаю изолированное Python-окружение…'
  "$PYTHON_BIN" -m venv "$ROOT/.venv"
fi
PYTHON="$ROOT/.venv/bin/python"
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r "$ROOT/requirements.txt" -r "$ROOT/requirements-desktop.txt"
"$PYTHON" "$ROOT/ensure_config.py" --migrate-data
# Existing Telegram users keep the connector after upgrades. New accounts must
# opt in so that core installs neither pull cryptography packages nor ask for
# personal Telegram credentials unexpectedly.
if (( WITH_TELEGRAM )) || [[ -s "$CONFIG_HOME/purah/telegram.env" ]]; then
  echo 'Устанавливаю необязательный Telegram API connector (Telethon)…'
  "$PYTHON" -m pip install -r "$ROOT/requirements-telegram.txt"
fi

# Desktop metadata is useful for launchers; actual HUD startup remains start-purah.sh -> Quickshell.
APP_DIR="$DATA_HOME/applications"
mkdir -p "$APP_DIR"
cat > "$APP_DIR/purah.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Purah
GenericName=Voice Assistant
Comment=Sheikah desktop voice companion
Exec="$ROOT/start-purah.sh"
Icon=$ROOT/assets/sheikah_crest.svg
Terminal=false
Categories=Utility;Accessibility;
StartupNotify=false
EOF
chmod 644 "$APP_DIR/purah.desktop"

printf '\n2/5 · Устанавливаю офлайн-фразу пробуждения (Vosk, русская модель)…\n'
"$ROOT/setup_voice.sh"

printf '\n3/5 · Выбери движок озвучки\n'
printf '  1) Edge TTS    — естественный русский голос, мало RAM, нужен интернет (по умолчанию)\n'
printf '  2) Piper       — локально/offline, умеренная RAM, голос из ONNX-модели (~63 MB)\n'
printf '  3) eSpeak NG   — очень лёгкий/offline, роботизированный и смешной голос\n'
printf '  4) Kokoro-ru   — локально/offline, естественнее, но наиболее требовательный к RAM\n'
printf '  5) Без озвучки — только текст\n\n'
CURRENT_ENGINE="$("$PYTHON" - <<'PY'
import os, tomllib
from pathlib import Path
config = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "purah" / "config.toml"
try:
    print(tomllib.loads(config.read_text(encoding="utf-8")).get("tts", {}).get("engine", "edge"))
except Exception:
    print("edge")
PY
)"
case "$CURRENT_ENGINE" in
  edge) DEFAULT_CHOICE=1 ;;
  piper) DEFAULT_CHOICE=2 ;;
  espeak_ng) DEFAULT_CHOICE=3 ;;
  kokoro_ru) DEFAULT_CHOICE=4 ;;
  none) DEFAULT_CHOICE=5 ;;
  *) DEFAULT_CHOICE=1 ;;
esac
choice="$DEFAULT_CHOICE"
if [[ -t 0 ]]; then
  read -r -p "Номер движка [$DEFAULT_CHOICE]: " entered_choice
  entered_choice="${entered_choice:-$DEFAULT_CHOICE}"
  if [[ "$entered_choice" =~ ^[1-5]$ ]]; then
    choice="$entered_choice"
  else
    echo "Неверный номер; сохраняю текущий движок $CURRENT_ENGINE."
  fi
else
  echo "Нет интерактивного терминала; сохраняю текущий движок $CURRENT_ENGINE."
fi

case "$choice" in
  1)
    "$PYTHON" "$ROOT/configure_tts.py" --engine edge --voice ru-RU-SvetlanaNeural --warmup-on-start false
    ;;
  2)
    printf '\nСкачиваю и проверяю Piper + русский голос Irina…\n'
    "$ROOT/setup_piper_tts.sh"
    ;;
  3)
    "$PYTHON" "$ROOT/configure_tts.py" --engine espeak_ng --espeak-voice ru --warmup-on-start false
    ;;
  4)
    printf '\nУстанавливаю Kokoro-ru. Здесь будут скачаны дополнительные Python-пакеты и ONNX/G2P-данные.\n'
    "$ROOT/setup_kokoro_ru.sh"
    ;;
  5)
    "$PYTHON" "$ROOT/configure_tts.py" --engine none --warmup-on-start false
    ;;
esac

printf '\n4/5 · Проверяю настройки и файлы…\n'
"$PYTHON" "$ROOT/ensure_config.py"
if ! "$PYTHON" "$ROOT/doctor.py" --strict; then
  echo 'PURAH: обязательные компоненты не готовы; проверь список выше и повтори ./install.sh --check.' >&2
  exit 1
fi
"$PYTHON" - <<'PY'
import os, tomllib
from pathlib import Path
cfg = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "purah" / "config.toml"
data = tomllib.loads(cfg.read_text(encoding="utf-8"))
print(f"TTS engine: {data.get('tts', {}).get('engine', 'edge')}")
print(f"Wake-word: {'enabled' if data.get('wakeword', {}).get('enabled', True) else 'disabled'}")
PY

printf '\n5/5 · Готово.\n'
cat <<EOF

PURAH установлена: $ROOT
Конфиг:            $CONFIG_HOME/purah/config.toml
Модель wake-word:  $DATA_HOME/purah/vosk-model-small-ru-0.22
Локальные голоса:  $DATA_HOME/purah/

Запуск:             $ROOT/start-purah.sh
Проверка TTS:       $PYTHON $ROOT/test_tts_playback.py
Проверка проекта:   $ROOT/check.sh
Проверка установки: $ROOT/install.sh --check
Telegram (опционально): $PYTHON $ROOT/setup_telegram.py

Hyprland автозапуск (добавь в ~/.config/hypr/hyprland.conf):
  exec-once = $ROOT/start-purah.sh

TTS переключается в [tts].engine в config.toml: edge, piper, espeak_ng, kokoro_ru, none.
Если существует ~/.config/purah/config.toml, он является единственным источником настроек: пропущенные ключи берутся из config.toml.example; shell env и ~/.config/purah/env не переопределяют их.
После изменения модели/API перезапусти Purah через этот скрипт. Активную конфигурацию
без вывода ключа можно проверить командой:
  $PYTHON $ROOT/main.py --print-effective-config
Прогрев Kokoro отключён ради RAM; модель грузится при первой озвучке. Для остальных
параметров используй config.toml.
EOF
