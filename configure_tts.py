#!/usr/bin/env python3
"""Update only selected [tts] keys while preserving the rest of the user's config."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import tomllib
from pathlib import Path

CONFIG = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))).expanduser() / "purah" / "config.toml"


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def update_key(block: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^([ \t]*{re.escape(key)}[ \t]*=)[^\n]*$")
    if pattern.search(block):
        return pattern.sub(lambda m: m.group(1) + " " + value, block, count=1)
    return block.rstrip() + f"\n{key} = {value}\n"


def update_config(updates: dict[str, str], path: Path = CONFIG) -> None:
    raw = path.read_text(encoding="utf-8")
    parsed = tomllib.loads(raw)
    if not isinstance(parsed.get("tts"), dict):
        raise RuntimeError(f"В {path} отсутствует секция [tts]. Сначала запусти ./ensure_config.py")
    if not updates:
        return
    backup = path.with_suffix(".toml.pre-tts-installer")
    if not backup.exists():
        shutil.copy2(path, backup)
    headers = list(re.finditer(r"(?m)^\[([^\]]+)\]\s*$", raw))
    head = next((m for m in headers if m.group(1).strip() == "tts"), None)
    if head is None:
        raise RuntimeError(f"В {path} отсутствует секция [tts].")
    next_head = next((m for m in headers if m.start() > head.start()), None)
    end = next_head.start() if next_head else len(raw)
    block = raw[head.start():end]
    for key, value in updates.items():
        block = update_key(block, key, value)
    updated = raw[:head.start()] + block + raw[end:]
    tomllib.loads(updated)
    path.write_text(updated, encoding="utf-8")
    os.chmod(path, 0o600)
    print(f"Настройки TTS сохранены в {path}")
    print(f"Резервная копия: {backup}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=("edge", "piper", "espeak_ng", "kokoro_ru", "none"))
    parser.add_argument("--voice", help="Edge TTS voice id")
    parser.add_argument("--piper-model", help="Path to Piper ONNX model")
    parser.add_argument("--espeak-voice", help="eSpeak NG voice name")
    parser.add_argument("--kokoro-voice", choices=("masha", "sveta"))
    parser.add_argument("--warmup-on-start", choices=("true", "false"), default="false")
    args = parser.parse_args()

    updates = {
        "engine": quote(args.engine),
        "warmup_on_start": args.warmup_on_start,
    }
    if args.voice:
        updates["voice"] = quote(args.voice)
    if args.piper_model:
        updates["piper_model"] = quote(str(Path(args.piper_model).expanduser()))
    if args.espeak_voice:
        updates["espeak_voice"] = quote(args.espeak_voice)
    if args.kokoro_voice:
        updates["kokoro_voice"] = quote(args.kokoro_voice)
    update_config(updates)
    print(f"Выбран TTS: {args.engine}")
    if args.engine == "piper":
        print("Piper берёт голос из ONNX-модели; параметр [tts].voice не используется.")
    elif args.engine == "espeak_ng":
        print("eSpeak NG полностью локальный и очень лёгкий; голос/скорость можно менять в [tts].")
    elif args.engine == "edge":
        print("Edge TTS требует интернет и использует сетевой голос из [tts].voice.")
    elif args.engine == "kokoro_ru":
        print("Kokoro выбран, но автопрогрев при запуске выключен: модель загрузится при первой озвучке.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
