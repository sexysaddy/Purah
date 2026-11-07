# Обновление PURAH 1.3.0 → 1.4.0

Рефакторинг совместим с прежними скриптами запуска: точка входа остаётся `main.py`, пользовательский `~/.config/purah/config.toml` не перезаписывается; недостающие ключи приходят из шаблона и миграции.

## Изменения

1. В `purah_agent/ui.py` перенесены `SheikahEye`, `SheikahBubble`, `PurahWindow`, `PurahTray` и `PurahEscapeFilter`. Модельный QThread теперь в `purah_agent/model_worker.py`, TTS/QThread — в `purah_agent/tts_worker.py`. Зависимости UI внедряются через `UiServices`. `main.py` стал меньше, но всё ещё содержит API, STT/Vosk и регистрацию инструментов.
2. AT-SPI GI: `inspect_accessibility` и `accessibility_action`, точный PID+Hyprland address, привязка к доступному top-level окну, короткоживущие snapshot IDs, строгий список действий и отказ при неоднозначности.
3. CDP: `browser_list_tabs`, `browser_read_tab` и `browser_navigate_tab` для явно разрешённого отдельного debug-профиля Chromium; LSP/AST/ctags/rg в `purah_agent/editor.py`; `file_manager_reveal` через FileManager1 D-Bus.
4. Tool-call SQLite-журнал `tool_events`: время, имя функции, статус, короткие метаданные, ограниченный текст stdout некоторых инструментов. Историю можно отключить через `[memory]` или стереть из меню очистки истории PURAH.
5. `visible_terminal_restore_focus = true`: возврат в прошлое окно при первом запуске managed-терминала, без перехвата фокуса, который пользователь сам переместил. Отдельно исправлен случай tmux-сессии, оставшейся после закрытия видимого терминала: явное «открой терминал» снова открывает окно.
6. Shell и ripgrep больше не собирают безграничный объём вывода в оперативной памяти, сокращена запись SSE-диагностики, уточнены правила фокуса и маршрутизации tool calling.

## Что не проверено на headless-машине

Реальная отрисовка/озвучка в PySide6+Quickshell, AT-SPI дерева твоего Telegram/редактора, Hyprland focus timing, tmux с твоим эмулятором, Chrome CDP с твоим профилем, D-Bus сервис файлового менеджера, Telegram API и внешние DeepSeek/STT/TTS endpoints. Изменения покрыты unit-тестами, но эти испытания нужно выполнить в пользовательской сессии Linux.

## Миграция/откат

Сохрани старый 1.3.0 архив. Перед заменой закрой текущую PURAH из трея; распакуй новую версию в каталог проекта и запусти `./install.sh` (или `./.venv/bin/python -m pip install -r requirements-desktop.txt`, если все системные зависимости уже на месте). Настройки `[browser]`, `[editor]` и `[memory]` необязательны. Для отката верни старые исходники — созданная в 1.4.0 таблица `tool_events` не мешает 1.3.0 и может оставаться в SQLite.
