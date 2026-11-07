# Execution Memory (checkpoint-resume) — план реализации

Черновик плана, чтобы не переизобретать при следующем заходе.
Основание: проверено по коду 1.4.2.

## 0. Главный принцип

**Никаких `task_checkpoint` как инструмента модели.** Все чекпоинты пишутся
программно в agent-цикле из уже полученного JSON-ответа инструмента.
Ноль дополнительных запросов к API. Модель видит задачу только через сводку
в system-промпте (и опционально через `task_resume` / `task_close`).

Почему: инструмент-чекпоинт = +1 round-trip к модели на каждый шаг, это
x1.5–2 по токенам на задаче из 10 шагов. Программная запись = 0.

## 1. Честность статусов (то, ради чего это вообще делается)

Статусы шага: `started` → `succeeded` / `failed` / `unknown`.

Правила вывода статуса из результата `execute_agent_tool` (JSON):
- `ok == true` → `succeeded`
- `ok == false` и в ответе есть `error` → `failed`
- `ok == false` и `timed_out == true` / `cancelled == true` / нет `ok` → `unknown`
- вызов прерван по `cancel_event` до возврата → `started` (не `failed`!)
- `run_terminal_command` с `mode=visible` и `returncode == null` → `unknown`
  (команда могла выполниться в tmux, а фокус уже уехал)

`unknown` — нормальное состояние, не стыдное. Дополнительно у шага есть
поле `verify_hint` (например `session=purah-agent`, `path=...`), чтобы при
resume было чем проверить: `tmux has-session`, `test -e`, `pgrep`.

## 2. Схема БД

Файл: `DB_PATH` = `$XDG_DATA_HOME/purah/purah_memory.sqlite3`
(`main.py:141`, `_user_data_dir()` → `main.py:67`).
Создаётся в `init_db()` (`main.py:448`), туда же добавляем вызов
`task_memory.initialize(db)` рядом с `tool_journal.initialize(db)` (`main.py:482`).

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,           -- uuid4 hex, живёт дольше запроса
    goal          TEXT NOT NULL,              -- формулировка от пользователя
    status        TEXT NOT NULL,              -- active|paused|done|failed|abandoned
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    closed_at     REAL,
    total_steps   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status, updated_at);

CREATE TABLE IF NOT EXISTS task_steps (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    turn_id       TEXT NOT NULL,              -- main.py:2411, один запрос
    step_no       INTEGER NOT NULL,
    tool_name     TEXT NOT NULL,
    args_summary  TEXT NOT NULL,              -- тот же redaction, что tool_journal
    result_summary TEXT NOT NULL,             -- только метаданные, не content
    status        TEXT NOT NULL,              -- started|succeeded|failed|unknown
    verify_hint   TEXT NOT NULL DEFAULT '',
    started_at    REAL NOT NULL,
    finished_at   REAL
);
CREATE INDEX IF NOT EXISTS ix_steps_task ON task_steps(task_id, id);
```

Ретеншн: как у журнала — держим последние N задач (по умолчанию 50),
шаги старше 30 дней чистим. Чистка в том же `append()`, best effort.

## 3. Новый модуль `purah_agent/task_memory.py`

Повторяет стиль `purah_agent/tool_journal.py` (там же брать `_redact`,
`_SAFE_METADATA`, `_SECRET_TERMS` — вынести в общий хелпер или импортировать
как приватные из `tool_journal`).

Публичный API (минимум):

```python
initialize(connection)                      # CREATE TABLE, вызывается из init_db
start_task(db_path, goal, turn_id) -> str   # task_id; одна активная задача на turn_id
record_step(db_path, task_id, turn_id, name, args, result,
            *, started_at, finished_at) -> str   # возвращает status
open_tasks(db_path, limit=3) -> list[dict]  # для сводки в промпт
summary_for_prompt(db_path, max_chars=600) -> str   # "" если нет открытых
resume(db_path, task_id) -> dict            # задача + хвост шагов + verify_hint
close_task(db_path, task_id, status, note="") -> dict
```

`record_step` обязан:
- не бросать исключений наружу (как журнал: `except` в вызывающем коде);
- считать `step_no` как `MAX(step_no)+1` для задачи;
- не хранить сырой `output` (только excerpt, как в журнале) и вообще ничего
  из `telegram_*`, браузера, accessibility, скриншотов.

## 4. Точки вставки в `main.py`

1. `init_db()` (:448) — добавить `task_memory.initialize(db)`.
2. `ask_model()` (:2335), после `turn_id = uuid.uuid4().hex` (:2411):
   - `active_task_id = task_memory.start_task(DB_PATH, latest_user, turn_id)`,
     но **только если** запрос похож на работу (есть tool-раунды). Проще:
     создавать задачу лениво — при первом `record_step` в этом `turn_id`.
     Ленивое создание лучше: болтовня не плодит задачи.
3. Agent-цикл `for _round in range(max_rounds + 1):` (:2477), блок
   `tool_output = execute_agent_tool(...)` + `tool_journal.append(...)`
   (:2524–2529) — сразу после журнала:
   ```python
   if TASK_MEMORY_ENABLED:
       try:
           task_memory.record_step(DB_PATH, task_id, turn_id, name, args, tool_output,
                                   started_at=call_started, finished_at=time.time())
       except (OSError, ValueError, sqlite3.Error, TypeError):
           pass
   ```
   `call_started` — `time.time()` непосредственно перед `execute_agent_tool` (:2524).
   `task_id` берётся из ленивого `start_task` в этом же `turn_id`.
4. Выход из `ask_model()` без `calls` (:2483, финальный ответ) — если задача
   открыта, поставить ей `status='paused'` (не `done`: модель сказала текст,
   но пользователь может продолжить). `done` ставит только `task_close`.
5. Обрыв (`cancel_event` в :2478 / :2481) — оставить задачу `active`,
   а последний выполненный шаг уже имеет `started`/`unknown`. Ничего не
   переписывать в «успех».
6. System-промпт: рядом с блоком журнала (`main.py:~2395`, `if not use_vision
   and PURAH_TOOL_JOURNAL_ENABLED`) добавить:
   `ПРОДОЛЖАЮЩАЯСЯ ЗАДАЧА: <summary_for_prompt()>` — одна строка:
   цель, последние 3 шага со статусами, `unknown` помечены как «проверить».
   Бюджет ~150 токенов. Если задач нет — блок не добавляется вообще.

## 5. Опциональные инструменты (по умолчанию выключены)

Флаг `PURAH_TASK_TOOLS_ENABLED=false`. Только если включён — добавлять в
`purah_agent/tool_specs.py AGENT_TOOLS` и в `execute_agent_tool`:

- `task_list` — открытые задачи (id, goal, статус, время).
- `task_resume task_id` — полный хвост шагов + `verify_hint` для проверки.
- `task_close task_id status=done|failed|abandoned note` — закрыть.

Внимание: `test_tool_specs.py:15-30` требует, чтобы каждый инструмент из
`AGENT_TOOLS` имел ветку `if name == "..."` в `execute_agent_tool`, и наоборот.
Если делаем условную регистрацию, тест надо обновить (или всегда держать
инструменты в списке, а внутри диспетчера возвращать `ok:false` при выключенном
флаге — это дешевле и не ломает контракт).

## 6. Конфиг и переменные окружения

Все — через `os.getenv` + `config.toml` по общей схеме (`ensure_config.py`):
- `PURAH_TASK_MEMORY_ENABLED` (default `true`)
- `PURAH_TASK_TOOLS_ENABLED` (default `false`)
- `PURAH_TASK_SUMMARY_MAX_CHARS` (default `600`)
- `PURAH_TASK_RETENTION_TASKS` (default `50`)
- `PURAH_TASK_RETENTION_DAYS` (default `30`)

## 7. Interrupt vs cancel (2.2, делается в том же заходе — 30 минут)

Сейчас `_interrupt_current_turn()` (`purah_agent/ui.py:835`) — единственный
жёсткий путь: `model_worker.cancel()` + стоп TTS. Wake word (`ui.py:574`)
дёргает именно его. Нужно:

- новое понятие `pause`: выставить `pause_event`, дать циклу завершить
  текущий инструмент и выйти после него, задачу оставить `active` с шагом
  в статусе `unknown`, TTS просто замолкает;
- `cancel` (то, что есть) — рвёт `_chat_completion` на потоке (`main.py:2252`)
  и помечает незавершённый шаг `unknown`;
- в `ask_model` проверять `pause_event` между раундами и **после**
  `execute_agent_tool`, но **не** внутри него;
- wake word → `pause` по умолчанию; Escape / кнопка стоп → `cancel`.
- `ModelWorker` (`purah_agent/model_worker.py`) получает второй `Event`
  и отдельный метод `pause()`.

## 8. Что НЕ делать

- Не писать `task_checkpoint` инструментом модели.
- Не поднимать `max_tool_rounds=999` в отдельную тему этого захода, но
  помнить (`main.py:2476`): при большом числе шагов сводка задачи обязана
  быть компактной, иначе она сама станет проблемой.
- Не пытаться восстанавливать выполнение инструмента после рестарта:
  `unknown` — это и есть ответ. Проверка только через `verify_hint`.
- Не трогать `messages` и схему истории. Задача — отдельный объект.

## 9. Порядок работ

1. `purah_agent/task_memory.py`: схема + `record_step` + `summary_for_prompt`,
   плюс unit-тест на временной sqlite (`test_task_memory.py`): статусы,
   ленивое создание, ретеншн, красные строки не сохраняются.
2. `init_db` + хук в цикле (`main.py:2411`, `2477–2529`).
3. Сводка в system-промпт (`main.py:~2395`). Проверить, что при отсутствии
   задач промпт байт-в-байт прежний.
4. `pause`/`cancel` в `ui.py:835` + `model_worker.py` + wake word `ui.py:574`.
5. Инструменты `task_*` за флагом (диспетчер отдаёт `ok:false` при выключенном).
6. Прогнать: `test_tool_specs.py`, `test_full_access_tools.py`,
   `test_desktop_agent.py`, плюс новый тест модуля.
7. CHANGELOG + строка в `docs/ARCHITECTURE_RU.md`.

## 10. Ожидаемый расход токенов

- +0 запросов к API (всё внутри цикла).
- +150–200 токенов system-промпта, только когда есть открытая задача.
- На длинных задачах суммарно дешевле: сводка заменяет пересказ истории
  в следующем запросе.
- С `PURAH_TASK_TOOLS_ENABLED=true` — плюс схемы трёх инструментов (~300
  токенов в каждом запросе) и по одному round-trip на ручной `resume`.