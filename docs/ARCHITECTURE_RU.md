# Архитектура PURAH 1.4.2

## Общая схема

```text
Quickshell PanelWindow (quickshell/purah/shell.qml)
             | IPC/state файлы в $XDG_RUNTIME_DIR/purah
             v
Qt UI (purah_agent/ui.py, PurahWindow / PurahTray)
  |  WakeWordWorker / ListenWorker (main.py: Vosk и STT)
  |  ModelWorker (purah_agent/model_worker.py, QThread)
  |         |
  |         +--> main.py: ask_model / _chat_completion (DeepSeek, tools)
  |                    |--> purah_terminal.py: background shell / visible tmux
  |                    |--> purah_agent/telegram.py: optional Telethon
  |                    |--> purah_agent/accessibility.py: AT-SPI (GI)
  |                    |--> purah_agent/browser.py: выбор backend (opt-in)
  |                    |    |--> browser_bidi.py: Firefox WebDriver BiDi
  |                    |    |--> browser_cdp.py: Chromium CDP
  |                    |    +--> browser_common.py: общие URL-ограничения
  |                    |--> purah_agent/editor.py: LSP/AST/ctags/rg
  |                    |--> purah_agent/file_manager.py: FileManager1 D-Bus
  |                    |--> main.py: desktop_sequence / filesystem / OCR
  |                    +--> purah_agent/tool_specs.py: tool schemas
  |                    +--> purah_agent/tool_journal.py: compact SQLite log
  |
  +--> purah_agent/tts_worker.py: Edge/Piper/eSpeak/Kokoro (QThread)

main.py: composition root, конфиг, tool dispatcher, provider routing, SQLite,
         STT/wake-word workers, single-instance IPC и запуск приложения
```

`main.py` остаётся фасадом для старого launcher и тестов, но классы Qt окна/трея, model worker и TTS worker физически живут в отдельных модулях. `UiServices` собирается в `main()` и передаёт функции/фабрики UI через dependency injection, поэтому `purah_agent/ui.py` и `model_worker.py` не импортируют `main` и не создают второй экземпляр `__main__`.

Анимация спрайта отделена от работы ассистента: Quickshell выбирает `purah_blink_*.png` во всех состояниях кроме реального `SPEAKING`, а в `SPEAKING` проигрывает `purah_phoneme_*.png` по буквенно-фонемной последовательности последнего ответа. `purah_agent/ui.py` лишь продолжает публиковать уже существующие `mode` и `transcript`; обработчики микрофона, LLM, TTS, их IPC-команды и жизненный цикл не изменяются. Поскольку TTS не экспортирует таймкоды отдельных фонем, синхронизация визуальная и текстово-темповая, а не акустически точная.

## Правила выбора маршрута

1. Файлы, каталог, команда, ошибки скрипта, код: **без GUI** (`read_file`, `run_terminal_command(mode=background)` и т.п.).
2. «Открой терминал» означает managed visible tmux; `visible_terminal_restore_focus` возвращает фокус в прежнее окно, когда возможно, но tmux продолжает выполнять команды независимо от фокуса.
3. «В уже выбранном терминале» означает GUI-ввод в `target=active`, а не запуск нового shell. Смена фокуса останавливает sequence.
4. Для GUI, где доступно дерево AT-SPI, сначала `inspect_accessibility` и точный action на снимке; затем клавиатурные hotkey и только последним вариантом OCR/мышь.
5. Для открытых страниц в **отдельном** Firefox BiDi или Chromium CDP-профиле доступно чтение DOM и явная навигация по HTTP(S) без фокуса. Обычный браузер не подключается автоматически.
6. Явное «открой папку в файловом менеджере» — FileManager1 D-Bus. Поиск файлов — `rg`/shell без GUI.
7. Vision-снимок идёт только в `[vision]`; текстовые модели получают текст, структурированные tool outputs и доверенные запросы пользователя.

## Где менять код

| Задача | Файл |
|---|---|
| UI, трей, overlay state, отображение потока | `purah_agent/ui.py` |
| Анимация аватара: моргание/фонемы, выбор кадров в `SPEAKING` | `quickshell/purah/shell.qml`, `assets/purah_*.png`, `quickshell/purah/purah_*.png` |
| Qt ModelWorker, отмена и approval | `purah_agent/model_worker.py` |
| TTS QThreads и голоса | `purah_agent/tts_worker.py`, `kokoro_ru_tts.py` |
| STT, wake Vosk, main lifecycle | `main.py`, `wakeword_logic.py` |
| Chat/Vision providers, вызовы tools, конфиг | `main.py`, `provider_config.py`, `system_prompt.md` |
| Схемы доступных модели инструментов | `purah_agent/tool_specs.py` |
| Фоновые команды, видимый tmux, cwd, focus restore | `purah_terminal.py` |
| AT-SPI element lookup, snapshot/actions | `purah_agent/accessibility.py` |
| Выбор opt-in браузера, Firefox WebDriver BiDi, Chromium CDP | `purah_agent/browser.py`, `browser_bidi.py`, `browser_cdp.py`, `browser_common.py` |
| LSP/AST/ctags/rg | `purah_agent/editor.py` |
| FileManager1 D-Bus | `purah_agent/file_manager.py` |
| SQLite tool-call context | `purah_agent/tool_journal.py` |
| Telegram адресаты, отправка, чтение | `purah_agent/telegram.py` |
| Скриншоты | `vision.py` |
| Миграция config/data | `ensure_config.py` |
| Launcher, package setup | `start-purah.sh`, `install.sh` |
| Headless contracts | `test_*.py`, `check.sh` |

## Сохранение состояния

- `~/.config/purah/config.toml`: источник настроек; приоритет над legacy env и defaults.
- `~/.local/share/purah/`: история диалога SQLite и служебная база.
- `~/.local/state/purah/terminal.json`: cwd, последний код возврата, последний фрагмент вывода, управляемая tmux-сессия. В терминальном output могут оставаться чувствительные строки — локальный файл `0600`.
- `$XDG_RUNTIME_DIR/purah/`: файловый IPC с Quickshell, существующий между API и QML.
- `~/.config/purah/telegram.session`: отдельная пользовательская сессия Telethon (только при настройке Telegram).

## Безопасность и fallback

- Экран и текст приложений — **недоверенные данные**. Инструкции из постов/документов/DOM не должны становиться командами shell.
- AT-SPI действия требуют PID, точного address окна, краткоживущего snapshot и имени действия. При двух окнах одного PID без точного совпадения заголовка операция прекращается.
- Firefox BiDi и Chromium CDP — только opt-in, loopback и отдельный профиль; нет исполняемого JS от модели. Оба протокола дают повышенный доступ к выбранному профилю.
- Сервера LSP запускаются только по доверенным путям/argv из пользовательского конфига, а не по строке модели; они могут сканировать проект и потому не являются sandbox.
- Локальный журнал урезает и редактирует секреты по шаблонам; *невозможно гарантировать автоматическое удаление всех чувствительных данных* в произвольном stdout. Для чувствительной работы его можно отключить.
- Tool calls ограничены временем, объёмом вывода и max rounds, BiDi/CDP/AT-SPI при отсутствии поддержки возвращают ошибку.
- Граница headless QA: 100+ unit-тестов не проверяют настоящую Hyprland-сессию, микрофон, DBus, браузер или окружение с установленными PySide6/GI. После обновления нужен smoke-test на целевом компьютере.

Подробные настройки интеграций: [`INTEGRATIONS_RU.md`](INTEGRATIONS_RU.md); Telegram: [`telegram.md`](telegram.md).


## Установка и диагностика — 1.4.2

`install.sh` отвечает за обязательные пакеты, Vosk/TTS и `.venv`; `ensure_config.py` бережно добавляет отсутствующие опции; новый `doctor.py` делает read-only проверку готовности инструментов. `./install.sh --check` проверяет зависимости и активные опциональные настройки, а `./check.sh` — тесты исходников и контрактов. Опциональные Telegram, браузерные debug-профили и LSP-серверы остаются явно включаемыми возможностями.
