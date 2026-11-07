#!/usr/bin/env python3
"""Read-only check of PURAH installation and optional Linux backends.

Does not start browsers, access private tabs, load models or print API credentials.
Run with the project's .venv/bin/python, or via ./install.sh --check.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parent


def effective_config(*, config_home: Path | None = None) -> dict:
    home = config_home or Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    default = tomllib.loads((ROOT / "config.toml.example").read_text(encoding="utf-8"))
    target = home / "purah" / "config.toml"
    if target.is_file():
        custom = tomllib.loads(target.read_text(encoding="utf-8"))
        for section, value in custom.items():
            if isinstance(value, dict) and isinstance(default.get(section), dict):
                default[section].update(value)
            else:
                default[section] = value
    return default


def _module_present(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _atspi_present() -> bool:
    try:
        from purah_agent.accessibility import _load_atspi
        return _load_atspi() is not None
    except Exception:
        return False


def _port_open(port: object) -> bool:
    try:
        port = int(port)
        if not 1024 <= port <= 65535:
            return False
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except (OSError, ValueError, TypeError):
        return False


def assess(config: dict | None = None) -> list[tuple[str, str, str]]:
    """Return (state, capability, instruction), without exposing secrets.

    state: OK | MISSING | OPTIONAL | CONFIGURE | INACTIVE.
    """
    cfg = config or effective_config()
    items: list[tuple[str, str, str]] = []

    def check(condition: bool, name: str, remedy: str, *, required: bool = True):
        items.append(("OK" if condition else ("MISSING" if required else "OPTIONAL"), name, "" if condition else remedy))

    check(sys.version_info >= (3, 11), "Python >=3.11", "Установи Python 3.11+.")
    check(Path(sys.prefix).resolve() == (ROOT / ".venv").resolve(),
          "Виртуальное окружение PURAH", "Запусти ./install.sh, затем ./install.sh --check.")
    for module, pip_name in (("PySide6", "PySide6"), ("sounddevice", "sounddevice"),
                             ("numpy", "numpy"), ("edge_tts", "edge-tts"),
                             ("vosk", "vosk"), ("websocket", "websocket-client")):
        check(_module_present(module), f"Python: {pip_name}",
              "Запусти ./install.sh (установка в локальную .venv).")

    # Hyprland + Quickshell overlay is PURAH's actual launch path. The
    # other utilities are used by its CLI tools or by the installer.
    for binary, remedy in (("qs", "quickshell"), ("hyprctl", "hyprland"),
                           ("Xwayland", "xorg-xwayland"), ("grim", "grim"),
                           ("wtype", "wtype"), ("tesseract", "tesseract"),
                           ("tmux", "tmux"), ("rg", "ripgrep"),
                           ("gdbus", "glib2"), ("espeak-ng", "espeak-ng")):
        check(bool(shutil.which(binary)), f"Команда: {binary}",
              f"На Arch: sudo pacman -S --needed {remedy}.")
    if shutil.which("tesseract"):
        try:
            output = subprocess.run(["tesseract", "--list-langs"], capture_output=True,
                                    text=True, timeout=3, check=False)
            langs = set(output.stdout.splitlines()) | set(output.stderr.splitlines())
            check({"rus", "eng"} <= langs, "OCR: русский и английский языки",
                  "На Arch: sudo pacman -S --needed tesseract-data-rus tesseract-data-eng.", required=False)
        except (OSError, subprocess.TimeoutExpired):
            check(False, "OCR: языковые данные", "Проверь tesseract --list-langs.", required=False)
    desktop = cfg.get("desktop", {}) if isinstance(cfg.get("desktop"), dict) else {}
    if desktop.get("enabled", True):
        check(_atspi_present(), "AT-SPI / Python gi (семантическое управление)",
              "Arch: sudo pacman -S at-spi2-core python-gobject gobject-introspection; "
              "нужен тот же Python ABI, что и у .venv.", required=False)
    if desktop.get("allow_mouse", False):
        check(bool(shutil.which("ydotool")), "Резервное управление мышью ydotool",
              "sudo pacman -S ydotool; systemctl --user enable --now ydotool.service")

    browser = cfg.get("browser", {}) if isinstance(cfg.get("browser"), dict) else {}
    for backend, enabled, port, executable in (
        ("Firefox BiDi", browser.get("firefox_bidi_enabled", False), browser.get("firefox_bidi_port", 9223), "firefox"),
        ("Chromium CDP", browser.get("cdp_enabled", False), browser.get("cdp_port", 9222), "chromium"),
    ):
        if not enabled:
            items.append(("OPTIONAL", backend, "Отключён в [browser]; включай только с отдельным профилем."))
            continue
        if backend == "Chromium CDP":
            available = any(shutil.which(x) for x in ("chromium", "google-chrome-stable", "brave"))
        else:
            available = bool(shutil.which(executable))
        check(available, backend + " — браузер установлен", "Установи браузер или отключи backend.")
        listening = _port_open(port)
        items.append(("OK" if listening else "INACTIVE", backend + " — локальный порт",
                      "" if listening else "Запусти отдельный debug-профиль; см. docs/INTEGRATIONS_RU.md."))

    editor = cfg.get("editor", {}) if isinstance(cfg.get("editor"), dict) else {}
    for language in ("python", "rust", "typescript"):
        raw_command = str(editor.get(f"lsp_{language}_command", "") or "").strip()
        if not raw_command:
            items.append(("OPTIONAL", f"LSP: {language}", "Не настроен. Python AST / rg работают без LSP."))
        else:
            try:
                command = shlex.split(raw_command)[0]
            except (ValueError, IndexError):
                command = ""
            check(bool(command and shutil.which(command)), f"LSP: {language}",
                  f"Команда {command or '<empty>'} недоступна. Установи language server или очисти настройку.")

    config_home = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "purah"
    telegram_env = config_home / "telegram.env"
    if telegram_env.is_file():
        check(_module_present("telethon"), "Telegram Telethon",
              "./install.sh --with-telegram или ./.venv/bin/python -m pip install -r requirements-telegram.txt")
        items.append(("CONFIGURE", "Telegram session", "Настроенная сессия проверяется при реальном входе; секреты не читаются диагностикой."))
    else:
        items.append(("OPTIONAL", "Telegram", "Фоновый API выключен; ./install.sh --with-telegram и ./.venv/bin/python setup_telegram.py."))

    # Credentials cannot be installed by pip or pacman; indicate missing user
    # setup without ever printing a key (or silently borrowing the chat key).
    api = cfg.get("api", {}) if isinstance(cfg.get("api"), dict) else {}
    chat_key = str(api.get("api_key", "") or "").strip()
    if not chat_key or chat_key.startswith("PUT_YOUR_"):
        items.append(("CONFIGURE", "Chat API key", "Заполни [api].api_key в ~/.config/purah/config.toml."))
    speech = cfg.get("speech", {}) if isinstance(cfg.get("speech"), dict) else {}
    if not str(speech.get("stt_api_key", "") or "").strip():
        items.append(("CONFIGURE", "STT API key", "Для распознавания речи настрой [speech].stt_api_key."))
    vision = cfg.get("vision", {}) if isinstance(cfg.get("vision"), dict) else {}
    if vision.get("enabled", True) and not vision.get("inherit_api", False) and \
            not str(vision.get("api_key", "") or "").strip():
        items.append(("CONFIGURE", "Vision API key", "Для снимков экрана настрой [vision].api_key."))

    tts = cfg.get("tts", {}) if isinstance(cfg.get("tts"), dict) else {}
    if tts.get("enabled", True):
        engine = str(tts.get("engine", "edge") or "edge")
        if engine == "piper":
            model = Path(str(tts.get("piper_model", ""))).expanduser()
            check(_module_present("piper") and model.is_file() and Path(str(model) + ".json").is_file(),
                  "Piper TTS", "Запусти ./setup_piper_tts.sh.")
        elif engine == "kokoro_ru":
            base = Path(str(tts.get("kokoro_model_dir", "~/.local/share/purah/kokoro-ru"))).expanduser()
            voice = str(tts.get("kokoro_voice", "masha") or "masha")
            files = [base / "onnx/model_quantized.onnx", base / "config.json", base / "kokoro-config.json",
                     base / "ru_g2p.py", base / "voices" / f"{voice}.bin"]
            check(all(item.is_file() for item in files) and _module_present("onnxruntime")
                  and _module_present("misaki") and _module_present("ruaccent"),
                  "Kokoro-ru TTS", "Запусти ./setup_kokoro_ru.sh.")

    wake = cfg.get("wakeword", {}) if isinstance(cfg.get("wakeword"), dict) else {}
    if wake.get("enabled", True):
        path = Path(str(wake.get("model_path", "~/.local/share/purah/vosk-model-small-ru-0.22"))).expanduser()
        check((path / "am/final.mdl").is_file(), "Vosk wake-word model",
              "Запусти ./setup_voice.sh для скачивания русской модели.")
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка установки PURAH (без sudo и изменения файлов)")
    parser.add_argument("--strict", action="store_true", help="Код 1, если не готова обязательная возможность")
    args = parser.parse_args(argv)
    try:
        items = assess()
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"PURAH: ошибка чтения конфига: {exc}", file=sys.stderr)
        return 2
    for state, name, note in items:
        print(f"[{state:9}] {name}" + (f" — {note}" if note else ""))
    required_missing = sum(state == "MISSING" for state, _, _ in items)
    print(f"\nОбязательных проблем: {required_missing}; необязательные интеграции настраиваются отдельно.")
    return 1 if required_missing and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
