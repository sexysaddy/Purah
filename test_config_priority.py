"""Regression tests: explicit config.toml settings beat stale environment and env-file values."""
from __future__ import annotations

import ast
import os
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")
_TREE = ast.parse(SOURCE)


def _function_from_main(name: str):
    node = next(n for n in _TREE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    namespace = {
        "os": os, "sys": sys, "re": re, "shlex": shlex, "Path": Path,
        "APP_DIR": ROOT, "CONFIG_AUTHORITATIVE_ENV": set(),
    }
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return namespace[name], namespace


class ConfigPriorityTests(unittest.TestCase):
    def test_explicit_toml_overrides_stale_chat_vision_tts_and_terminal_environment(self):
        with tempfile.TemporaryDirectory(prefix="purah-config-test-") as temporary:
            config_dir = Path(temporary) / "purah"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text('''
[api]
chat_url = "https://api.deepseek.com/chat/completions"
model = "deepseek-flash"
api_key = "config-key-placeholder"
enable_tools = true
thinking_mode = "enabled"

[vision]
chat_url = ""
model = ""
api_key = ""

[tts]
engine = "piper"

[terminal]
enabled = true
full_access = true
allow_sudo = true
visible_terminal_restore_focus = false

[browser]
automation_backend = "firefox"
firefox_bidi_enabled = true
firefox_bidi_port = 9444
cdp_enabled = true
cdp_port = 9333

[editor]
lsp_python_command = "pyright-langserver --stdio"

[memory]
tool_journal_enabled = false
tool_context_max_events = 2
''', encoding="utf-8")
            env_path = config_dir / "env"
            env_path.write_text('''PURAH_API_URL=https://old-provider.invalid/v1/chat/completions
PURAH_MODEL=old-chat-model
PURAH_VISION_URL=https://old-vision.invalid/v1/chat/completions
PURAH_VISION_MODEL=old-vision-model
PURAH_TTS_ENGINE=kokoro_ru
PURAH_TERMINAL_FULL_ACCESS=false
''', encoding="utf-8")
            stale = {
                "XDG_CONFIG_HOME": temporary,
                "PURAH_API_URL": "https://stale-shell.invalid/chat/completions",
                "PURAH_MODEL": "stale-shell-model",
                "PURAH_VISION_URL": "https://stale-shell-vision.invalid/chat/completions",
                "PURAH_VISION_MODEL": "stale-shell-vision-model",
                "PURAH_TTS_ENGINE": "edge",
                "PURAH_TERMINAL_FULL_ACCESS": "false",
                "PURAH_VISIBLE_TERMINAL_RESTORE_FOCUS": "true",
                "PURAH_BROWSER_AUTOMATION_BACKEND": "chromium",
                "PURAH_BROWSER_FIREFOX_BIDI_ENABLED": "false",
                "PURAH_BROWSER_FIREFOX_BIDI_PORT": "9223",
                "PURAH_BROWSER_CDP_ENABLED": "false",
                "PURAH_BROWSER_CDP_PORT": "9222",
                "PURAH_TOOL_JOURNAL_ENABLED": "true",
                "PURAH_THINKING_MODE": "disabled",
                "OPENAI_API_KEY": "stale-shell-key",
                "PURAH_API_KEY": "stale-legacy-key",
                "CHOOM_MODEL": "legacy-shell-model",
                "CHOOM_API_URL": "https://legacy-shell.invalid/chat/completions",
            }
            with mock.patch.dict(os.environ, stale, clear=True):
                load_config, ns = _function_from_main("load_user_config")
                load_env, env_ns = _function_from_main("load_user_env")
                bridge, bridge_ns = _function_from_main("_bridge_legacy_environment")
                ns["_user_config_dir"] = lambda: config_dir
                env_ns["_user_config_dir"] = lambda: config_dir
                load_config()
                load_env()
                bridge()
                self.assertEqual(os.environ["PURAH_API_URL"], "https://api.deepseek.com/chat/completions")
                self.assertEqual(os.environ["PURAH_MODEL"], "deepseek-flash")
                self.assertEqual(os.environ["PURAH_API_URL"], "https://api.deepseek.com/chat/completions")
                self.assertNotEqual(os.environ["PURAH_MODEL"], "legacy-shell-model")
                self.assertEqual(os.environ["PURAH_VISION_URL"], "")
                self.assertEqual(os.environ["PURAH_VISION_MODEL"], "")
                self.assertEqual(os.environ["PURAH_VISION_INHERIT_API"], "false")
                self.assertEqual(os.environ["PURAH_TTS_ENGINE"], "piper")
                self.assertEqual(os.environ["PURAH_TERMINAL_FULL_ACCESS"], "true")
                self.assertEqual(os.environ["PURAH_VISIBLE_TERMINAL_RESTORE_FOCUS"], "false")
                self.assertEqual(os.environ["PURAH_BROWSER_AUTOMATION_BACKEND"], "firefox")
                self.assertEqual(os.environ["PURAH_BROWSER_FIREFOX_BIDI_ENABLED"], "true")
                self.assertEqual(os.environ["PURAH_BROWSER_FIREFOX_BIDI_PORT"], "9444")
                self.assertEqual(os.environ["PURAH_BROWSER_CDP_ENABLED"], "true")
                self.assertEqual(os.environ["PURAH_BROWSER_CDP_PORT"], "9333")
                self.assertEqual(os.environ["PURAH_EDITOR_LSP_PYTHON_COMMAND"], "pyright-langserver --stdio")
                self.assertEqual(os.environ["PURAH_TOOL_JOURNAL_ENABLED"], "false")
                self.assertEqual(os.environ["PURAH_TOOL_CONTEXT_MAX_EVENTS"], "2")
                self.assertEqual(os.environ["PURAH_THINKING_MODE"], "enabled")
                self.assertEqual(os.environ["OPENAI_API_KEY"], "config-key-placeholder")
                self.assertNotIn("PURAH_API_KEY", os.environ)
                self.assertIn("OPENAI_API_KEY", ns["CONFIG_AUTHORITATIVE_ENV"])

    def test_config_defaults_beat_shell_and_env_for_keys_absent_from_toml(self):
        with tempfile.TemporaryDirectory(prefix="purah-config-test-") as temporary:
            config_dir = Path(temporary) / "purah"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text('[api]\nmodel = "configured-model"\n', encoding="utf-8")
            (config_dir / "env").write_text('PURAH_MODEL=env-model\nPURAH_STT_LANGUAGE=en\nPURAH_TTS_ENGINE=kokoro_ru\n', encoding="utf-8")
            stale = {
                "XDG_CONFIG_HOME": temporary,
                "PURAH_MODEL": "shell-model",
                "PURAH_API_URL": "https://stale-provider.invalid/chat/completions",
                "PURAH_STT_LANGUAGE": "de",
                "PURAH_TTS_ENGINE": "piper",
                "PURAH_API_KEY": "stale-key",
                "OPENAI_API_KEY": "stale-openai-key",
            }
            with mock.patch.dict(os.environ, stale, clear=True):
                load_config, ns = _function_from_main("load_user_config")
                load_env, env_ns = _function_from_main("load_user_env")
                ns["_user_config_dir"] = lambda: config_dir
                env_ns["_user_config_dir"] = lambda: config_dir
                load_config()
                load_env()
                self.assertEqual(os.environ["PURAH_MODEL"], "configured-model")
                self.assertEqual(os.environ["PURAH_API_URL"], "https://api.deepseek.com/chat/completions")
                self.assertEqual(os.environ["PURAH_BROWSER_AUTOMATION_BACKEND"], "auto")
                self.assertEqual(os.environ["PURAH_BROWSER_FIREFOX_BIDI_ENABLED"], "false")
                self.assertEqual(os.environ["PURAH_BROWSER_FIREFOX_BIDI_PORT"], "9223")
                self.assertEqual(os.environ["PURAH_STT_LANGUAGE"], "ru")
                self.assertEqual(os.environ["PURAH_TTS_ENGINE"], "edge")
                self.assertEqual(os.environ["OPENAI_API_KEY"], "PUT_YOUR_DEEPSEEK_API_KEY_HERE")
                self.assertNotIn("PURAH_API_KEY", os.environ)

    def test_legacy_inline_system_prompt_is_not_shadowed_by_template_file_pointer(self):
        with tempfile.TemporaryDirectory(prefix="purah-prompt-priority-") as temporary:
            config_dir = Path(temporary) / "purah"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                '[api]\nmodel = "custom-model"\nchat_url = "https://chat.example/v1/chat/completions"\n'
                '[assistant]\nsystem_prompt = "LEGACY CUSTOM PROMPT"\n',
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary}, clear=True):
                load_config, namespace = _function_from_main("load_user_config")
                # This helper's APP_DIR points to the real application directory and supplies
                # defaults, reproducing the old bug where default system_prompt_file overrode
                # an explicit inline prompt from the user's config.
                namespace["_user_config_dir"] = lambda: config_dir
                load_config()
                self.assertEqual(os.environ["PURAH_SYSTEM_PROMPT"], "LEGACY CUSTOM PROMPT")

    def test_explicit_prompt_file_wins_over_inline_text_when_both_are_configured(self):
        with tempfile.TemporaryDirectory(prefix="purah-prompt-file-") as temporary:
            config_dir = Path(temporary) / "purah"
            config_dir.mkdir()
            prompt = Path(temporary) / "external-prompt.md"
            prompt.write_text("PROMPT FROM FILE", encoding="utf-8")
            import json
            (config_dir / "config.toml").write_text(
                '[api]\nmodel = "custom-model"\nchat_url = "https://chat.example/v1/chat/completions"\n'
                '[assistant]\nsystem_prompt = "INLINE TEXT"\nsystem_prompt_file = ' + json.dumps(str(prompt)) + '\n',
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary}, clear=True):
                load_config, namespace = _function_from_main("load_user_config")
                namespace["_user_config_dir"] = lambda: config_dir
                load_config()
                self.assertEqual(os.environ["PURAH_SYSTEM_PROMPT"], "PROMPT FROM FILE")

    def test_invalid_toml_fails_closed_instead_of_falling_back_to_old_provider(self):
        with tempfile.TemporaryDirectory(prefix="purah-config-test-") as temporary:
            config_dir = Path(temporary) / "purah"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text('[api\nmodel = "broken-model"\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {
                "XDG_CONFIG_HOME": temporary,
                "PURAH_API_URL": "https://stale-provider.invalid/chat/completions",
                "PURAH_MODEL": "stale-model",
            }, clear=True):
                load_config, ns = _function_from_main("load_user_config")
                ns["_user_config_dir"] = lambda: config_dir
                with self.assertRaises(SystemExit) as error:
                    load_config()
                self.assertIn("PURAH CONFIG ERROR", str(error.exception))
                self.assertEqual(os.environ["PURAH_MODEL"], "stale-model")
                # App termination is the fail-closed guarantee: this stale value is never used.

    def test_explicit_empty_chat_url_without_base_url_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="purah-config-test-") as temporary:
            config_dir = Path(temporary) / "purah"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text('[api]\nchat_url = ""\nmodel = "configured-model"\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {
                "XDG_CONFIG_HOME": temporary,
                "PURAH_API_URL": "https://stale-provider.invalid/chat/completions",
            }, clear=True):
                load_config, ns = _function_from_main("load_user_config")
                ns["_user_config_dir"] = lambda: config_dir
                with self.assertRaises(SystemExit) as error:
                    load_config()
                self.assertIn("base_url не задан", str(error.exception))

    def test_deepseek_tool_calls_preserve_reasoning_content(self):
        self.assertIn('((api, "thinking_mode"), "PURAH_THINKING_MODE")', SOURCE)
        self.assertIn('payload_obj["thinking"] = {"type": thinking_mode}', SOURCE)
        self.assertIn('delta.get("reasoning_content", "")', SOURCE)
        self.assertIn('assistant_call_message["reasoning_content"] = reasoning_content', SOURCE)
        self.assertIn('if deepseek_thinking and thinking_mode == "enabled":', SOURCE)
        self.assertNotIn('payload_obj["thinking"] = {"type": "disabled"}', SOURCE,
                         'legacy override must not silently disable the configured DeepSeek thinking mode')
        self.assertIn('"PUT_YOUR_DEEPSEEK_API_KEY_HERE"', SOURCE,
                      'default placeholder must not be treated as a valid API key')

    def test_deepseek_placeholder_is_not_a_valid_api_key(self):
        node = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_has_real_api_key")
        module = ast.Module(body=[node], type_ignores=[])
        namespace = {"_API_KEY_PLACEHOLDERS": {
            "replace_me", "REPLACE_WITH_FRESH_API_KEY", "YOUR_API_KEY", "ВАШ_API_KEY",
            "PUT_YOUR_API_KEY_HERE", "PUT_YOUR_DEEPSEEK_API_KEY_HERE", "PASTE_API_KEY_HERE",
        }}
        exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
        self.assertFalse(namespace["_has_real_api_key"]("PUT_YOUR_DEEPSEEK_API_KEY_HERE"))
        self.assertTrue(namespace["_has_real_api_key"]("sk-test-not-a-real-key-0000"))

    def test_stale_environment_override_switch_is_not_supported(self):
        source = SOURCE
        self.assertNotIn("PURAH_TTS_ENV_OVERRIDE", source)
        self.assertIn("config.toml then replaces all settings", source)


if __name__ == "__main__":
    unittest.main()
