"""Regression tests for installer flags, upgrade configuration and read-only doctor."""
from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import doctor
import ensure_config as ensure

ROOT = Path(__file__).resolve().parent


class InstallReadinessTests(unittest.TestCase):
    def test_legacy_config_receives_all_new_options_without_overwriting_preferences(self):
        with tempfile.TemporaryDirectory(prefix="purah-settings-upgrade-") as temp:
            config_dir = Path(temp)
            path = config_dir / "config.toml"
            path.write_text('''[api]
chat_url = "https://custom.example/v1"
model = "custom-model"
api_key = "custom-private-value"

[terminal]
full_access = false
visible_terminal_restore_focus = false

[browser]
firefox_bidi_enabled = true
firefox_bidi_port = 9345

[editor]
lsp_python_command = "pyright-langserver --stdio"
''', encoding="utf-8")
            with (mock.patch.object(ensure, "CONFIG_DIR", config_dir),
                  mock.patch.object(ensure, "CONFIG_PATH", path),
                  mock.patch.object(ensure, "EXAMPLE_PATH", ROOT / "config.toml.example")):
                ensure.add_missing_settings()
                first = path.read_text(encoding="utf-8")
                ensure.add_missing_settings()
                self.assertEqual(path.read_text(encoding="utf-8"), first, "Upgrade must be idempotent")
            config = tomllib.loads(first)
            self.assertEqual(config["api"]["api_key"], "custom-private-value")
            self.assertFalse(config["terminal"]["full_access"])
            self.assertFalse(config["terminal"]["visible_terminal_restore_focus"])
            self.assertTrue(config["browser"]["firefox_bidi_enabled"])
            self.assertEqual(config["browser"]["firefox_bidi_port"], 9345)
            self.assertEqual(config["browser"]["cdp_port"], 9222)
            self.assertFalse(config["browser"]["cdp_enabled"])
            self.assertEqual(config["browser"]["automation_backend"], "auto")
            self.assertEqual(config["editor"]["lsp_python_command"], "pyright-langserver --stdio")
            self.assertEqual(config["editor"]["lsp_rust_command"], "")
            self.assertIn("memory", config)
            self.assertTrue(config["memory"]["tool_journal_enabled"])
            self.assertEqual(config["memory"]["tool_context_max_events"], 5)
            self.assertIn("ui", config)
            self.assertTrue(path.with_suffix(".toml.pre-purah-update").is_file())

    def test_old_install_gets_visible_terminal_focus_setting(self):
        with tempfile.TemporaryDirectory(prefix="purah-focus-upgrade-") as temp:
            config_dir = Path(temp)
            path = config_dir / "config.toml"
            path.write_text('[terminal]\nenabled = true\n', encoding="utf-8")
            with mock.patch.object(ensure, "CONFIG_DIR", config_dir), mock.patch.object(ensure, "CONFIG_PATH", path):
                ensure.add_missing_settings()
            cfg = tomllib.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(cfg["terminal"]["visible_terminal_restore_focus"])

    def test_doctor_reports_configured_firefox_and_missing_lsp(self):
        cfg = tomllib.loads((ROOT / "config.toml.example").read_text(encoding="utf-8"))
        cfg["browser"]["firefox_bidi_enabled"] = True
        cfg["browser"]["firefox_bidi_port"] = 9345
        cfg["editor"]["lsp_rust_command"] = "rust-analyzer"
        with (mock.patch.object(doctor, "_module_present", return_value=True),
              mock.patch.object(doctor, "_atspi_present", return_value=True),
              mock.patch.object(doctor, "_port_open", return_value=False) as port_open,
              mock.patch.object(doctor.shutil, "which", side_effect=lambda name: None if name == "rust-analyzer" else "/usr/bin/" + name),
              mock.patch.object(doctor.subprocess, "run", return_value=type("R", (), {"stdout": "eng\nrus\n", "stderr": ""})()),
              mock.patch.object(doctor.sys, "prefix", str(ROOT / ".venv")),
              mock.patch.object(doctor.sys, "version_info", (3, 13))):
            results = doctor.assess(cfg)
        self.assertIn(("INACTIVE", "Firefox BiDi — локальный порт",
                       "Запусти отдельный debug-профиль; см. docs/INTEGRATIONS_RU.md."), results)
        self.assertTrue(any(s == "MISSING" and n == "LSP: rust" for s, n, _ in results))
        port_open.assert_called_once_with(9345)

    def test_doctor_strict_fails_only_on_required_missing(self):
        with mock.patch.object(doctor, "assess", return_value=[
            ("OK", "Quickshell", ""), ("OPTIONAL", "Telegram", "disabled"),
        ]):
            self.assertEqual(doctor.main(["--strict"]), 0)
        with mock.patch.object(doctor, "assess", return_value=[("MISSING", "qs", "install it")]):
            self.assertEqual(doctor.main(["--strict"]), 1)
            self.assertEqual(doctor.main([]), 0)

    def test_telegram_loader_never_executes_env_file(self):
        shell = (ROOT / "start-purah.sh").read_text(encoding="utf-8")
        self.assertIn('TELEGRAM_ENV="${XDG_CONFIG_HOME:-$HOME/.config}/purah/telegram.env"', shell)
        self.assertIn("while IFS='=' read -r name value", shell)
        self.assertNotIn('source "$TELEGRAM_ENV"', shell)
        self.assertNotIn('eval "$TELEGRAM_ENV"', shell)

    def test_installer_core_deps_and_telegram_are_explicit(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("--check", script)
        self.assertIn("--with-telegram", script)
        self.assertIn("requirements-desktop.txt", script)
        self.assertIn("requirements-telegram.txt", script)
        self.assertIn("doctor.py", script)
        self.assertIn("python-gobject", script)
        self.assertIn("at-spi2-core", script)
        self.assertIn("ripgrep", script)
        self.assertIn("glib2", script)
        self.assertIn("tmux", script)


if __name__ == "__main__":
    unittest.main()
