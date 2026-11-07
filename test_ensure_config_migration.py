"""Tests the upgrade path for the old stock provider configuration."""
from __future__ import annotations

import importlib.util
import tempfile
import tomllib
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("purah_ensure_config_under_test", ROOT / "ensure_config.py")
assert SPEC and SPEC.loader
ensure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ensure)


class EnsureConfigMigrationTests(unittest.TestCase):
    def test_old_audio_and_tool_defaults_migrate_in_their_own_toml_tables(self):
        with tempfile.TemporaryDirectory(prefix="purah-default-tuning-") as temporary:
            config_dir = Path(temporary)
            config_path = config_dir / "config.toml"
            # Deliberately unusual table order: TOML keys must remain scoped to their table.
            config_path.write_text("""[speech]
language = "ru"
vad_threshold = 180
end_silence_seconds = 0.65
max_record_seconds = 20
speech_start_timeout_seconds = 7

[assistant]
system_prompt = "MY INLINE PROMPT"

[api]
chat_url = "https://chat.example/v1/chat/completions"
model = "my-model"
api_key = "my-key"
max_tool_rounds = 4

[vision]
chat_url = "https://api.groq.com/openai/v1/chat/completions"
model = "qwen/qwen3.8-27b"
api_key = ""
inherit_api = false
""", encoding="utf-8")
            ensure.CONFIG_DIR = config_dir
            ensure.CONFIG_PATH = config_path
            ensure.EXAMPLE_PATH = ROOT / "config.toml.example"
            ensure.add_missing_settings()
            parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["speech"]["vad_threshold"], 100)
            self.assertEqual(parsed["speech"]["end_silence_seconds"], 1.15)
            self.assertEqual(parsed["speech"]["max_record_seconds"], 45)
            self.assertEqual(parsed["speech"]["speech_start_timeout_seconds"], 8)
            self.assertEqual(parsed["api"]["max_tool_rounds"], 12)
            self.assertEqual(parsed["assistant"]["system_prompt"], "MY INLINE PROMPT")
            self.assertNotIn("system_prompt_file", parsed["assistant"])
            self.assertIn("desktop", parsed)
            self.assertTrue(config_path.with_suffix(".toml.pre-purah-update").is_file())

    def test_old_shipped_groq_default_migrates_to_deepseek_and_keeps_custom_key(self):
        with tempfile.TemporaryDirectory(prefix="purah-config-migration-") as temporary:
            home = Path(temporary)
            config_dir = home / "config"
            config_dir.mkdir()
            config_path = config_dir / "config.toml"
            config_path.write_text('''[api]
chat_url = "https://api.groq.com/openai/v1/chat/completions"
model = "openai/gpt-oss-120b"
api_key = "my-custom-key"
temperature = 0.35

[vision]
enabled = true
chat_url = ""
model = "qwen/qwen3.8-27b"
api_key = ""
''', encoding="utf-8")
            ensure.CONFIG_DIR = config_dir
            ensure.CONFIG_PATH = config_path
            ensure.EXAMPLE_PATH = ROOT / "config.toml.example"
            ensure.add_missing_settings()
            parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["api"]["chat_url"], "https://api.deepseek.com/chat/completions")
            self.assertEqual(parsed["api"]["model"], "deepseek-flash")
            self.assertEqual(parsed["api"]["api_key"], "my-custom-key")
            self.assertEqual(parsed["vision"]["chat_url"], "https://api.groq.com/openai/v1/chat/completions")
            self.assertEqual(parsed["vision"]["model"], "qwen/qwen3.8-27b")
            self.assertFalse(parsed["vision"]["inherit_api"])
            self.assertEqual(parsed["vision"]["api_key"], "")
            self.assertTrue((config_path.with_suffix(".toml.pre-purah-update")).is_file())

    def test_custom_provider_is_not_replaced_and_missing_provider_values_are_added(self):
        with tempfile.TemporaryDirectory(prefix="purah-config-custom-") as temporary:
            config_dir = Path(temporary)
            config_path = config_dir / "config.toml"
            config_path.write_text('[api]\nchat_url = "https://custom.example/v1/chat/completions"\nmodel = "custom-model"\napi_key = "custom-key"\n', encoding="utf-8")
            ensure.CONFIG_DIR = config_dir
            ensure.CONFIG_PATH = config_path
            ensure.EXAMPLE_PATH = ROOT / "config.toml.example"
            ensure.add_missing_settings()
            parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["api"]["chat_url"], "https://custom.example/v1/chat/completions")
            self.assertEqual(parsed["api"]["model"], "custom-model")
            self.assertEqual(parsed["api"]["api_key"], "custom-key")
            self.assertTrue(parsed["api"]["enable_tools"])
            self.assertIn("thinking_mode", parsed["api"])

    def test_blank_legacy_vision_provider_is_migrated_to_separate_route_without_copying_chat_key(self):
        with tempfile.TemporaryDirectory(prefix="purah-config-vision-migration-") as temporary:
            config_dir = Path(temporary)
            config_path = config_dir / "config.toml"
            config_path.write_text('''[api]
chat_url = "https://api.deepseek.com/chat/completions"
model = "deepseek-flash"
api_key = "deepseek-key"

[vision]
enabled = true
chat_url = ""
model = ""
api_key = ""
''', encoding="utf-8")
            ensure.CONFIG_DIR = config_dir
            ensure.CONFIG_PATH = config_path
            ensure.EXAMPLE_PATH = ROOT / "config.toml.example"
            ensure.add_missing_settings()
            parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["api"]["api_key"], "deepseek-key")
            self.assertEqual(parsed["vision"]["chat_url"], "https://api.groq.com/openai/v1/chat/completions")
            self.assertEqual(parsed["vision"]["model"], "qwen/qwen3.8-27b")
            self.assertEqual(parsed["vision"]["api_key"], "")
            self.assertFalse(parsed["vision"]["inherit_api"])
            self.assertTrue(config_path.with_suffix(".toml.pre-purah-update").is_file())

    def test_existing_speech_config_gets_separate_stt_api_key(self):
        with tempfile.TemporaryDirectory(prefix="purah-stt-key-migration-") as temporary:
            config_dir = Path(temporary) / ".config" / "purah"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "config.toml"
            config_path.write_text(
                '[api]\nchat_url = "https://api.deepseek.com/chat/completions"\nmodel = "deepseek-flash"\napi_key = "DEEPSEEK_TEST"\n'
                '\n[speech]\nstt_url = "https://api.groq.com/openai/v1/audio/transcriptions"\nstt_model = "whisper-large-v3-turbo"\n'
                '\n[vision]\nchat_url = "https://api.groq.com/openai/v1/chat/completions"\nmodel = "qwen/qwen3.8-27b"\napi_key = ""\n',
                encoding="utf-8",
            )
            with mock.patch.object(ensure, "CONFIG_DIR", config_dir), mock.patch.object(ensure, "CONFIG_PATH", config_path):
                ensure.add_missing_settings()
            parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["speech"]["stt_api_key"], "")
            self.assertEqual(parsed["api"]["api_key"], "DEEPSEEK_TEST")
            self.assertFalse(parsed["vision"]["inherit_api"])


if __name__ == "__main__":
    unittest.main()
