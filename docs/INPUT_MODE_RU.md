# Режим ввода: микрофон / текст

Добавлено: 2026-11-06.

## Что это

Одно состояние `input_mode` с двумя значениями:

- `voice` — как раньше: wake word, barge-in, follow-up, кнопка микрофона запускает запись.
- `text` — микрофон полностью выключен: wake-воркер не запускается, фраза «Привет, Пура» игнорируется, follow-up не армится, запись не открывается ни по одному сигналу. Клик по значку/трею/хоткею сразу открывает диалог с полем ввода.

## Где переключается

1. Кнопка микрофона в диалоге Quickshell — теперь это mute-тумблер (`toggle_input_mode`). В текстовом режиме перечёркнута и красная.
2. Трей → подменю «Режим ввода» → «Микрофон (голос)» / «Текст (клавиатура)».
3. Клик по иконке в трее: в `voice` — как раньше (запись), в `text` — открыть диалог.
4. Конфиг `[ui] input_mode = "voice" | "text"` — стартовое значение.

## Файлы

- `purah_agent/ui.py`
  - `PurahWindow.input_mode`, `_load_input_mode`, `_save_input_mode`, `set_input_mode`, `_sync_input_mode_ui`, `open_text_dialog`.
  - Гейты `input_mode != "voice"` в `start_wake_listener`, `_maybe_resume_wakeword`, `_arm_wake_followup`, `_maybe_run_pending_listen`, `start_listening`.
  - `activate_listen_request` и `toggle_listening` в текстовом режиме уходят в `open_text_dialog`.
  - `PurahTray`: `mode_menu`, `mode_group` (QActionGroup), `voice_action`, `text_action`, `sync_input_mode`.
  - `ui-state.json` теперь публикует `input_mode`; `ui-command.json` принимает `toggle_input_mode` и `set_input_mode`.
- `quickshell/purah/shell.qml` — `ui.input_mode`, mute-кнопка, статус «РЕЖИМ ТЕКСТА», клик по глазу в текстовом режиме шлёт `expand`.
- `main.py` — маппинг `[ui].input_mode` → `PURAH_INPUT_MODE`.
- `config.toml.example` — ключ с комментарием.
- `test_input_mode.py` — контрактные тесты.

## Поведение состояния

- Режим сохраняется в `$PURAH_RUNTIME_DIR/input-mode.json` (runtime, не конфиг), чтобы пережить перезапуск в рамках сессии.
- Конфиг задаёт только стартовое значение; ручное переключение его не перезаписывает.
- `wakeword_paused` остался как отдельный флаг, но из трея убран: его роль теперь выполняет `input_mode = text`.

## Проверка

```
.venv/bin/python -m unittest test_input_mode test_quickshell_hud test_wakeword_logic
qmllint quickshell/purah/shell.qml
```

Живой прогон требует перезапуска PURAH: `./start-purah.sh`.
