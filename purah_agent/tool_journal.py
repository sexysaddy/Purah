"""Compact, local, bounded SQLite journal for non-sensitive tool outcomes.

Tool output is *untrusted* and this journal isn't a full transcript. Credentials,
message bodies, private file content and screenshots are intentionally excluded.
"""
from __future__ import annotations
import json
import re
from contextlib import closing
import sqlite3
import time
from pathlib import Path

_SAFE_METADATA = {"mode", "cwd", "path", "target", "session", "backend", "returncode",
                  "timed_out", "cancelled", "output_truncated", "action", "method", "error", "title"}
_CONTENT_TOOLS = {"run_terminal_command", "read_terminal_output", "editor_code_symbols", "editor_search_code"}
_SECRETS = [
    re.compile(r"(?i)\b(?:Bearer\s+)[A-Za-z0-9._~+/-]{8,}"),
    re.compile(r"(?i)\b(?:sk|ghp|gho|glpat|xox[baprs])-[a-z0-9_\-]{8,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|password|passwd|secret)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\b(?:authorization)\s*:\s*[^\n]+"),
]
_SECRET_TERMS = re.compile(r"(?i)(token|secret|password|credential|cookie|session|private.key|id_rsa|api.key)")


def _redact(value: str) -> str:
    for expression in _SECRETS:
        value = expression.sub("[REDACTED]", value)
    return value


def summarize_tool(name: str, arguments: dict, result: str) -> tuple[str, str, bool]:
    """Return redacted, bounded summaries without preserving raw arbitrary input."""
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        data = {"ok": False, "error": "Non-JSON tool output"}
    if not isinstance(data, dict):
        data = {"ok": False, "error": "Invalid tool output"}
    safe_args = {}
    for key in ("mode", "cwd", "path", "target", "session", "action"):
        if isinstance(arguments, dict) and key in arguments:
            value = str(arguments[key])[:120]
            if not _SECRET_TERMS.search(value):
                safe_args[key] = _redact(value)
    info = {"ok": bool(data.get("ok", False))}
    for key in _SAFE_METADATA:
        value = data.get(key)
        if key != "session" and isinstance(value, (str, int, float, bool)):
            text = str(value)[:240]
            info[key] = "[REDACTED]" if _SECRET_TERMS.search(text) else _redact(text)
    if name in _CONTENT_TOOLS and not _SECRET_TERMS.search(str(arguments)):
        text = data.get("output", "")
        if isinstance(text, str) and not _SECRET_TERMS.search(text):
            info["output_excerpt"] = _redact(text[:800])
    # Never retain Telegram recipient IDs/message bodies, browser DOM, raw file data,
    # accessibility text or screenshots between restarts.
    return json.dumps(safe_args, ensure_ascii=False), json.dumps(info, ensure_ascii=False), bool(data.get("ok"))


def initialize(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS tool_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        turn_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        args_summary TEXT NOT NULL,
        result_summary TEXT NOT NULL,
        success INTEGER NOT NULL,
        created_at REAL NOT NULL
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_tool_events_created ON tool_events(created_at)")


def append(db_path: Path, turn_id: str, name: str, args: dict, result: str) -> None:
    args_safe, summary, success = summarize_tool(name, args, result)
    with closing(sqlite3.connect(db_path, timeout=3.0)) as db, db:
        db.execute("INSERT INTO tool_events(turn_id, tool_name, args_summary, result_summary, success, created_at) "
                   "VALUES (?,?,?,?,?,?)", (turn_id, name[:80], args_safe, summary, int(success), time.time()))
        db.execute("DELETE FROM tool_events WHERE id NOT IN (SELECT id FROM tool_events ORDER BY id DESC LIMIT 300)")
        db.execute("DELETE FROM tool_events WHERE created_at < ?", (time.time() - 7 * 86400,))


def recent(db_path: Path, *, limit: int = 5, max_age_hours: float = 24, max_chars: int = 3000) -> list[dict]:
    if not db_path.is_file():
        return []
    with closing(sqlite3.connect(db_path, timeout=3.0)) as db:
        try:
            rows = db.execute("SELECT tool_name, args_summary, result_summary, created_at "
                              "FROM tool_events WHERE created_at >= ? ORDER BY id DESC LIMIT ?",
                              (time.time() - max_age_hours * 3600, max(0, min(10, limit)))).fetchall()
        except sqlite3.OperationalError:
            return []
    items = []
    remaining = max(0, min(6000, max_chars))
    for name, args, result, ts in rows:
        record = {"tool": name, "args": json.loads(args), "result": json.loads(result), "age_seconds": int(time.time()-ts)}
        size = len(json.dumps(record, ensure_ascii=False))
        if size <= remaining:
            items.append(record)
            remaining -= size
    return items
