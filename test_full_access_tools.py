"""Exercise filesystem tools' full-access behavior without importing the Qt UI."""
from __future__ import annotations

import ast
import json
import os
import shutil
import time
import uuid
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def build_tools(app_dir: Path, data_dir: Path):
    names = {"tool_write_file", "tool_create_directory", "tool_delete_path"}
    nodes = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {
        "os": os, "json": json, "shutil": shutil, "time": time, "uuid": uuid,
        "Path": Path, "APP_DIR": app_dir, "MAX_TOOL_WRITE_CHARS": 1_000_000,
        "_user_data_dir": lambda: data_dir,
    }

    def tool_path(raw_path: str, *, must_exist: bool = False) -> Path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = app_dir / candidate
        return candidate.resolve(strict=must_exist)

    def inside_project(path: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(app_dir.resolve())
            return True
        except ValueError:
            return False

    def require_project_path(path: Path) -> None:
        if not inside_project(path):
            raise PermissionError(f"outside project: {path}")

    namespace.update({
        "_tool_path": tool_path,
        "_is_inside_project": inside_project,
        "_require_project_path": require_project_path,
        "_tool_result": lambda ok, **values: json.dumps({"ok": ok, **values}),
    })
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / "main.py"), "exec"), namespace)
    return namespace


class FullAccessFileToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="purah-full-access-")
        root = Path(self.temp.name)
        self.app_dir = root / "project"
        self.app_dir.mkdir()
        self.data_dir = root / "data" / "purah"
        self.tools = build_tools(self.app_dir, self.data_dir)

    def tearDown(self):
        self.temp.cleanup()

    def test_write_outside_project_is_allowed_and_backed_up_in_full_access(self):
        target = Path(self.temp.name) / "home" / ".config" / "hypr" / "hyprland.conf"
        target.parent.mkdir(parents=True)
        target.write_text("old config\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"PURAH_TERMINAL_FULL_ACCESS": "true"}, clear=False):
            result = json.loads(self.tools["tool_write_file"](str(target), "new config\n"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(target.read_text(encoding="utf-8"), "new config\n")
        backup = Path(result["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_text(encoding="utf-8"), "old config\n")

    def test_write_outside_project_is_rejected_when_full_access_is_off(self):
        target = Path(self.temp.name) / "outside.txt"
        with mock.patch.dict(os.environ, {"PURAH_TERMINAL_FULL_ACCESS": "false"}, clear=False):
            result = json.loads(self.tools["tool_write_file"](str(target), "nope"))
        self.assertFalse(result["ok"])
        self.assertFalse(target.exists())

    def test_create_directory_outside_project_requires_full_access(self):
        target = Path(self.temp.name) / "created-dir"
        with mock.patch.dict(os.environ, {"PURAH_TERMINAL_FULL_ACCESS": "false"}, clear=False):
            denied = json.loads(self.tools["tool_create_directory"](str(target)))
        self.assertFalse(denied["ok"])
        with mock.patch.dict(os.environ, {"PURAH_TERMINAL_FULL_ACCESS": "true"}, clear=False):
            allowed = json.loads(self.tools["tool_create_directory"](str(target)))
        self.assertTrue(allowed["ok"], allowed)
        self.assertTrue(target.is_dir())

    def test_delete_explicitly_requested_external_path_works_in_full_access(self):
        target = Path(self.temp.name) / "temporary-user-file.txt"
        target.write_text("to be removed", encoding="utf-8")
        with mock.patch.dict(os.environ, {"PURAH_TERMINAL_FULL_ACCESS": "true"}, clear=False):
            result = json.loads(self.tools["tool_delete_path"](str(target)))
        self.assertTrue(result["ok"], result)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
