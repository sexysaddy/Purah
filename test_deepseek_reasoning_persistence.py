"""Tests for hidden DeepSeek reasoning round-trips in the local SQLite history."""
from __future__ import annotations

import ast
import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from purah_agent import tool_journal

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def extracted(name: str, namespace: dict):
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return namespace[name]


class DeepSeekReasoningPersistenceTests(unittest.TestCase):
    def test_migrates_old_db_and_roundtrips_reasoning_without_touching_message_text(self):
        with tempfile.TemporaryDirectory(prefix="purah-reasoning-db-") as temporary:
            path = Path(temporary) / "purah.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
                db.execute("INSERT INTO messages(role, content) VALUES ('assistant', 'old answer')")

            namespace = {
                "DB_PATH": path,
                "sqlite3": sqlite3,
                "closing": closing,
                "_migrate_legacy_database": lambda: None,
                "Path": Path,
                "os": os,
                "tool_journal": tool_journal,
            }
            init_db = extracted("init_db", namespace)
            save_message = extracted("save_message", namespace)
            get_history = extracted("get_history", namespace)
            init_db()

            with closing(sqlite3.connect(path)) as db:
                self.assertIn("reasoning_content", {row[1] for row in db.execute("PRAGMA table_info(messages)")})
            self.assertEqual(get_history(), [{"role": "assistant", "content": "old answer"}])
            save_message("assistant", "new answer", reasoning_content="hidden provider state")
            save_message("user", "hello", reasoning_content="must not be saved for user")
            history = get_history()
            self.assertEqual(history[1], {
                "role": "assistant", "content": "new answer", "reasoning_content": "hidden provider state"
            })
            self.assertEqual(history[2], {"role": "user", "content": "hello"})

    def test_reasoning_is_hidden_from_ui_and_removed_for_non_thinking_providers(self):
        # UI reads only role/content, while HTTP payload strips this provider-specific field
        # unless the official DeepSeek API and thinking_mode=enabled are both active.
        self.assertIn('"SELECT role, content, reasoning_content FROM messages', SOURCE)
        self.assertIn('message.pop("reasoning_content", None)', SOURCE)
        self.assertIn('thinking_mode == "enabled"', SOURCE)
        ui_source = (ROOT / "purah_agent/ui.py").read_text(encoding="utf-8")
        self.assertIn('self.services.save_message("assistant", answer, reasoning_content=reasoning_content)', ui_source)


if __name__ == "__main__":
    unittest.main()
