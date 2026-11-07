#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  printf 'PURAH: не найдено окружение %s\nЗапусти ./install.sh один раз. При старте системы активировать venv вручную НЕ нужно.\n' "$ROOT/.venv" >&2
  exit 1
fi

# The always-on launcher must use a real Wayland layer shell. Never silently fall
# back to a regular Qt toplevel: that is exactly the blurred/3D-transformed window
# the overlay mode is intended to avoid.
if ! command -v qs >/dev/null 2>&1; then
  echo 'PURAH: не найден Quickshell (qs); запуск остановлен, обычное Qt-окно НЕ будет открыто.' >&2
  echo 'Установи его: sudo pacman -S --needed quickshell' >&2
  exit 2
fi

if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
  RUNTIME_DIR="$XDG_RUNTIME_DIR/purah"
else
  RUNTIME_DIR="/tmp/purah-${UID:-$(id -u)}"
fi
mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"
export PURAH_APP_DIR="$ROOT"
export PURAH_RUNTIME_DIR="$RUNTIME_DIR"
export PURAH_HISTORY_LIMIT="${PURAH_HISTORY_LIMIT:-12}"

# Optional Telegram connector credentials. Read as literal KEY=VALUE records,
# not executable shell: API hashes and session paths must never be eval'd.
# Respect XDG_CONFIG_HOME, exactly like setup_telegram.py and ensure_config.py.
TELEGRAM_ENV="${XDG_CONFIG_HOME:-$HOME/.config}/purah/telegram.env"
if [[ -r "$TELEGRAM_ENV" ]]; then
  while IFS='=' read -r name value || [[ -n "${name:-}" ]]; do
    case "$name" in
      PURAH_TELEGRAM_API_ID|PURAH_TELEGRAM_API_HASH|PURAH_TELEGRAM_SESSION)
        export "$name=$value" ;;
    esac
  done < "$TELEGRAM_ENV"
fi

"$PYTHON" "$ROOT/ensure_config.py" --migrate-data
APP_VERSION="$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' "$ROOT/main.py" | head -n 1)"
[[ -n "$APP_VERSION" ]] || { echo 'PURAH: невозможно определить версию приложения.' >&2; exit 2; }
QS_PIDFILE="$RUNTIME_DIR/quickshell.pid"
QS_VERSION_FILE="$RUNTIME_DIR/quickshell-version"
QS_READY_FILE="$RUNTIME_DIR/quickshell-ready"
QS_PROJECT="$ROOT/quickshell/purah"
QS_PID=""
QS_RUNNING=0

# The previous QML could fail before writing quickshell-ready, leaving stale
# Quickshell instances after repeated launch attempts. Only retire qs processes
# whose argv points at this exact Purah config, never other Quickshell shells.
stop_project_quickshells() {
  local proc pid cmd
  local pids=()
  for proc in /proc/[0-9]*; do
    pid="${proc##*/}"
    [[ "$pid" == "$$" || "$pid" == "$PPID" ]] && continue
    [[ -r "$proc/cmdline" ]] || continue
    cmd="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
    if [[ "$cmd" == *"qs -p $QS_PROJECT"* || "$cmd" == *"qs --path $QS_PROJECT"* ]]; then
      pids+=("$pid")
      kill "$pid" 2>/dev/null || true
    fi
  done
  if ((${#pids[@]})); then
    sleep 0.25
    local stale
    for stale in "${pids[@]}"; do
      if [[ -r "/proc/$stale/cmdline" ]]; then
        cmd="$(tr '\0' ' ' < "/proc/$stale/cmdline" 2>/dev/null || true)"
        if [[ "$cmd" == *"qs -p $QS_PROJECT"* || "$cmd" == *"qs --path $QS_PROJECT"* ]]; then
          kill -KILL "$stale" 2>/dev/null || true
        fi
      fi
    done
  fi
}

# Install live layer rules once per Hyprland instance, before mapping the QML
# surface. The hotkey can call this launcher repeatedly, so do not append duplicate
# rules on every voice interaction. A restarted Hyprland gets a new instance signature.
HYPR_INSTANCE="${HYPRLAND_INSTANCE_SIGNATURE:-unknown}"
HYPR_INSTANCE_SAFE="$(printf '%s' "$HYPR_INSTANCE" | tr -c 'A-Za-z0-9_.-' '_')"
RULE_MARKER="$RUNTIME_DIR/layerrules-${HYPR_INSTANCE_SAFE}-glass-blur-v2"
if [[ ! -e "$RULE_MARKER" ]] && command -v hyprctl >/dev/null 2>&1; then
  if hyprctl keyword layerrule "blur on, ignore_alpha 0.08, match:namespace ^purah-overlay$" >/dev/null 2>&1; then
    hyprctl keyword layerrule "blurpopups off, match:namespace ^purah-overlay$" >/dev/null 2>&1 || true
    hyprctl keyword layerrule "no_anim on, match:namespace ^purah-overlay$" >/dev/null 2>&1 || true
    : > "$RULE_MARKER"
    chmod 600 "$RULE_MARKER"
  else
    echo 'PURAH: не удалось применить live layerrule blur on; подключи hyprland-purah-rule.conf/.lua.' >&2
  fi
fi

# Reuse only the overlay process loaded from the current on-disk project version.
# A stale Quickshell process can keep running old QML after an in-place update.
if [[ -r "$QS_PIDFILE" ]]; then
  read -r QS_PID < "$QS_PIDFILE" || true
  if [[ "${QS_PID:-}" =~ ^[0-9]+$ ]] && kill -0 "$QS_PID" 2>/dev/null; then
    CMDLINE="$(tr '\0' ' ' < "/proc/$QS_PID/cmdline" 2>/dev/null || true)"
    if [[ "$CMDLINE" == *"$QS_PROJECT"* ]]; then
      if [[ -s "$QS_READY_FILE" && -s "$QS_VERSION_FILE" && "$(cat "$QS_VERSION_FILE")" == "$APP_VERSION" ]]; then
        QS_RUNNING=1
      else
        kill "$QS_PID" 2>/dev/null || true
        for _ in $(seq 1 20); do
          kill -0 "$QS_PID" 2>/dev/null || break
          sleep 0.05
        done
        if kill -0 "$QS_PID" 2>/dev/null; then
          # Do not allow two fullscreen layer-shell surfaces after an update.
          kill -KILL "$QS_PID" 2>/dev/null || true
          for _ in $(seq 1 10); do
            kill -0 "$QS_PID" 2>/dev/null || break
            sleep 0.05
          done
        fi
      fi
    fi
  fi
fi

if [[ "$QS_RUNNING" -eq 0 ]]; then
  stop_project_quickshells
  rm -f "$QS_PIDFILE" "$QS_READY_FILE" "$QS_VERSION_FILE"
  # Runtime log is for the current start attempt only; don't confuse old QML
  # errors with the current version when diagnosing startup failures.
  : > "$RUNTIME_DIR/quickshell.log"
  chmod 600 "$RUNTIME_DIR/quickshell.log"
  env -u QT_QPA_PLATFORM PURAH_APP_DIR="$ROOT" PURAH_RUNTIME_DIR="$RUNTIME_DIR" \
    qs -p "$QS_PROJECT" >>"$RUNTIME_DIR/quickshell.log" 2>&1 < /dev/null &
  QS_PID=$!
  printf '%s\n' "$QS_PID" > "$QS_PIDFILE"
  printf '%s\n' "$APP_VERSION" > "$QS_VERSION_FILE"
  chmod 600 "$QS_PIDFILE" "$QS_VERSION_FILE"
  for _ in $(seq 1 80); do
    if [[ -s "$QS_READY_FILE" ]] && kill -0 "$QS_PID" 2>/dev/null; then
      QS_RUNNING=1
      break
    fi
    if ! kill -0 "$QS_PID" 2>/dev/null; then break; fi
    sleep 0.1
  done
fi

if [[ "$QS_RUNNING" -ne 1 ]]; then
  echo 'PURAH: Quickshell layer overlay не запустился; обычное Qt-окно намеренно запрещено.' >&2
  echo "Лог Quickshell: $RUNTIME_DIR/quickshell.log" >&2
  tail -n 60 "$RUNTIME_DIR/quickshell.log" >&2 || true
  if [[ "${QS_PID:-}" =~ ^[0-9]+$ ]] && kill -0 "$QS_PID" 2>/dev/null; then kill "$QS_PID" 2>/dev/null || true; fi
  stop_project_quickshells
  rm -f "$QS_PIDFILE" "$QS_READY_FILE" "$QS_VERSION_FILE"
  exit 3
fi

export PURAH_LAYER_OVERLAY=1
export PURAH_REQUIRE_LAYER_OVERLAY=1
export QT_QPA_PLATFORM="${PURAH_QPA_PLATFORM:-xcb}"

# --listen starts one manual voice interaction with the same overlay; no argument
# starts the persistent wake-word daemon for Hyprland autostart.
if [[ "${1:-}" == "--listen" ]]; then
  shift
  exec "$PYTHON" "$ROOT/main.py" --listen "$@"
fi
exec "$PYTHON" "$ROOT/main.py" --tray "$@"
