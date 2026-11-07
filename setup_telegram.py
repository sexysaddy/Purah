#!/usr/bin/env python3
"""One-time local setup for PURAH's optional Telegram connector.

The API hash and session stay under ~/.config/purah with mode 0600.  This script
does not send credentials to PURAH's model API.  Obtain an API ID/hash from
https://my.telegram.org, then run this script in the PURAH virtualenv.
"""
from __future__ import annotations

import getpass
import os
import re
from pathlib import Path


def main() -> int:
    try:
        from telethon import TelegramClient
    except ImportError:
        print("Telethon не установлен. Выполни: python -m pip install -r requirements-telegram.txt")
        return 2
    raw_id = input("Telegram API ID: ").strip()
    if not raw_id.isdigit():
        print("API ID должен быть числом")
        return 2
    api_hash = getpass.getpass("Telegram API hash (не показывается): ").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        print("API hash должен содержать ровно 32 шестнадцатеричных символа")
        return 2
    phone = input("Номер Telegram в международном формате: ").strip()
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "purah"
    config_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(config_dir, 0o700)
    env_path = config_dir / "telegram.env"
    session_path = config_dir / "telegram.session"
    client = TelegramClient(str(session_path), int(raw_id), api_hash)
    try:
        client.start(phone=phone)
    except Exception as exc:
        print(f"Вход в Telegram не выполнен: {type(exc).__name__}")
        return 1
    finally:
        client.disconnect()
    env_path.write_text(
        f"PURAH_TELEGRAM_API_ID={raw_id}\n"
        f"PURAH_TELEGRAM_API_HASH={api_hash}\n"
        f"PURAH_TELEGRAM_SESSION={session_path}\n",
        encoding="utf-8",
    )
    os.chmod(env_path, 0o600)
    try:
        os.chmod(session_path, 0o600)
    except OSError:
        pass
    print(f"Готово. Локальные настройки сохранены в {env_path}")
    print("start-purah.sh автоматически подхватит telegram.env при следующем запуске PURAH.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

