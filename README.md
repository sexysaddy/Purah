# PURAH

Голосовой AI-компаньон для Linux/Hyprland: wake-word, отдельные chat/vision модели, TTS, локальная память и инструменты управления системой. Интерфейс — Quickshell layer-shell overlay со спрайтами и липсинком, логика — Python 3.11+, общение с моделью — через любой OpenAI-совместимый API (по умолчанию DeepSeek).

Актуальная версия — **1.4.2**. Историческое имя проекта — CHOOM.

## Что умеет

- **Голос и текст.** Локальная фраза пробуждения (Vosk, офлайн), запись с VAD, STT через отдельный провайдер (по умолчанию Groq/Whisper), либо полностью текстовый режим без микрофона.
- **Озвучка.** Edge TTS (по умолчанию), локальные Piper / eSpeak NG / Kokoro-ru, либо без звука. Выбор движка — при установке и в конфиге.
- **Управление системой как инструменты, а не как «нажми куда-нибудь».** AT-SPI для доступных приложений, Firefox WebDriver BiDi и Chromium CDP для отдельных debug-профилей, LSP/AST/`rg` для кода, D-Bus для файлового менеджера, MPRIS и Yandex Music для медиа.
- **Фоновые операции по умолчанию.** Чтение файлов, поиск по коду, `ls`, диагностика — без открытия окон и без перехвата фокуса. GUI-действия только по явному запросу.
- **Терминал.** Фоновый shell и управляемая tmux-сессия (`tmux -L purah`) с возвратом прежнего фокуса; окружение дочернего процесса очищается от переменных с ключами.
- **Telegram без GUI.** Необязательный коннектор на Telethon: отправка и чтение через локальную пользовательскую сессию, с обязательным подтверждением неоднозначных адресатов.
- **Память.** Локальный журнал вызовов инструментов в SQLite (урезанные результаты, без текста сообщений, файлов и скриншотов) плюс план execution-memory.
- **Зрение по запросу.** Скриншот уходит в отдельную vision-модель; чат-модель изображения не видит.

## Требования

- Arch Linux или производный дистрибутив (установщик системных пакетов рассчитан на `pacman`).
- Hyprland и Quickshell (`qs`). Без Quickshell приложение намеренно не запускается: обычное Qt-окно вместо layer-shell не используется.
- Python 3.11+, PortAudio, `wtype`, `grim`, `tesseract` (+ `rus`/`eng`), `tmux`, `ripgrep`, `gdbus`, `at-spi2-core`, `python-gobject`.
- Ключ chat-провайдера; для голосового ввода — ключ STT-провайдера (это разные сервисы).

## Установка

```bash
git clone https://github.com/sexysaddy/Purah.git
cd Purah
./install.sh
```

Установщик создаёт `.venv` внутри проекта, ставит зависимости, настраивает конфиг, фразу пробуждения и выбранный TTS-движок. Активировать venv вручную при входе в систему не нужно — `start-purah.sh` вызывает интерпретатор напрямую.

Опции:

```bash
./install.sh --check          # только диагностика, без sudo/pip/сети
./install.sh --with-telegram  # дополнительно Telethon, затем setup_telegram.py
```

Запуск:

```bash
./run-purah.sh        # обычный старт (Quickshell HUD)
./start-purah.sh --listen
```

## Настройка

Конфиг — `~/.config/purah/config.toml` (права `600`). Шаблон со всеми секциями и комментариями: [`config.toml.example`](config.toml.example).

```bash
cp config.toml.example ~/.config/purah/config.toml
chmod 600 ~/.config/purah/config.toml
```

Секции: `[api]`, `[speech]`, `[ui]`, `[terminal]`, `[assistant]`, `[desktop]`, `[browser]`, `[editor]`, `[memory]`, `[wakeword]`, `[tts]`, `[vision]`.

Ключи хранятся только в этом файле и в отдельных файлах пользователя (`~/.config/purah/telegram.env`, `~/.config/purah/yandex_token`) — в репозиторий они не попадают. Путь к токену Yandex Music можно переопределить через `PURAH_YANDEX_TOKEN_FILE`.

Секреты из shell-переменных и старых `CHOOM_*` не подхватываются: существующий `config.toml` — единственный источник истины.

### Смена провайдера

`[api].chat_url` + `model` + `api_key` — любой OpenAI-совместимый endpoint (LM Studio, Ollama, Groq и т.п.). Если бэкенд не умеет function calling, поставь `enable_tools = false` — но тогда теряется управление системой. STT настраивается отдельно в `[speech]`, чтобы смена chat-модели не ломала голосовой ввод.

## Инструменты модели

29 функций, контракты — [`purah_agent/tool_specs.py`](purah_agent/tool_specs.py):

| Группа | Инструменты |
| --- | --- |
| Файлы | `list_directory`, `read_file`, `write_file`, `create_directory`, `delete_path`, `file_manager_reveal` |
| Shell | `run_terminal_command`, `read_terminal_output` |
| Программы и веб | `launch_application`, `open_browser_tab`, `read_webpage` |
| GUI | `inspect_active_window`, `type_text_in_active_window`, `press_key_in_active_window`, `click_in_active_window`, `mouse_calibration`, `desktop_sequence` |
| AT-SPI | `inspect_accessibility`, `accessibility_action` |
| Браузер (BiDi/CDP) | `browser_list_tabs`, `browser_navigate_tab`, `browser_read_tab` |
| Код | `editor_code_symbols`, `editor_search_code` |
| Медиа | `media_control`, `music` |
| Telegram | `telegram_resolve_recipient`, `telegram_send_message`, `telegram_latest_message` |

Порядок предпочтения: нативный API приложения → AT-SPI → браузерная автоматизация в отдельном профиле → клавиатурная последовательность Hyprland → OCR/vision. Если инструмент не вернул данных, ассистент обязан сказать об этом, а не изображать успех.

## Структура проекта

```text
Purah/
├── main.py                  # composition root: конфиг, STT, API модели, dispatcher
├── purah_terminal.py        # фоновый shell, tmux, cwd, возврат фокуса
├── purah_agent/
│   ├── ui.py                # Qt/Quickshell HUD и IPC
│   ├── model_worker.py      # QThread запроса к модели
│   ├── tts_worker.py        # QThread TTS
│   ├── tool_specs.py        # контракты инструментов
│   ├── accessibility.py     # AT-SPI
│   ├── browser*.py          # BiDi / CDP / выбор backend
│   ├── editor.py            # LSP, AST, ctags, rg
│   ├── telegram.py          # фоновый коннектор
│   ├── music.py             # Yandex Music через mpv
│   └── tool_journal.py      # журнал вызовов
├── quickshell/purah/        # QML overlay, спрайты, липсинк
├── assets/                  # графика состояний
├── docs/                    # архитектура, интеграции, changelog
├── install.sh               # установка
├── start-purah.sh           # запуск HUD
├── check.sh                 # статическая проверка репозитория
└── doctor.py                # диагностика окружения
```

## Диагностика

```bash
./install.sh --check      # зависимости и конфиг, без изменений
./.venv/bin/python doctor.py --strict
./check.sh                # py_compile, bash -n, инварианты конфига и tool-specs
```

`INACTIVE` у браузерного backend означает только закрытый порт: открытый порт сам по себе ещё не подтверждает рабочую WebDriver/CDP-сессию.

## Безопасность и ограничения

- `[terminal].full_access = true` по умолчанию даёт модели shell-доступ в правах текущего пользователя без подтверждения каждой команды. Если это не то, что нужно — поставь `full_access = false`.
- Root-операции идут через `sudo -A`: пароль вводится только в локальном GUI-окне и никогда не уходит в API.
- Автоматизация браузера — только отдельные профили (`--new-instance --profile ... --remote-debugging-port=...`). Не включай remote debugging на повседневном профиле.
- Telegram-коннектор работает от твоей пользовательской сессии, а не от бота: сессия и `api_hash` лежат локально.
- Содержимое веб-страниц, файлов и скриншотов считается недоверенными данными, а не инструкциями.
- Проект заточен под Arch + Hyprland; на других окружениях часть адаптеров (layer-shell, `hyprctl`, `wtype`) работать не будет.

## Документация

- [`README_RU.md`](README_RU.md) — подробное руководство: настройки, TTS, размытие, окна, миграция.
- [`docs/ARCHITECTURE_RU.md`](docs/ARCHITECTURE_RU.md) — архитектура и потоки данных.
- [`docs/INTEGRATIONS_RU.md`](docs/INTEGRATIONS_RU.md) — desktop-интеграции и их ограничения.
- [`docs/INPUT_MODE_RU.md`](docs/INPUT_MODE_RU.md) — микрофон против текстового режима.
- [`docs/telegram.md`](docs/telegram.md) — коннектор Telegram.
- [`docs/TASK_MEMORY_PLAN_RU.md`](docs/TASK_MEMORY_PLAN_RU.md) — план execution-memory.
- [`docs/CHANGELOG_1_4_2_RU.md`](docs/CHANGELOG_1_4_2_RU.md) — последние изменения.

## Лицензия

Пока не указана.
