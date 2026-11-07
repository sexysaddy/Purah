import tempfile
import tomllib
import unittest
from pathlib import Path

from configure_tts import update_config


class ConfigureTTSTests(unittest.TestCase):
    def test_only_tts_values_change_and_backup_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.toml'
            config.write_text('''[api]\nmodel = "keep-model"\n\n[tts]\nengine = "kokoro_ru"\nvoice = "ru-RU-SvetlanaNeural"\nwarmup_on_start = true\n\n[assistant]\nsystem_prompt = "keep prompt"\n''', encoding='utf-8')
            update_config({'engine': '"piper"', 'warmup_on_start': 'false'}, config)
            parsed = tomllib.loads(config.read_text(encoding='utf-8'))
            self.assertEqual(parsed['tts']['engine'], 'piper')
            self.assertFalse(parsed['tts']['warmup_on_start'])
            self.assertEqual(parsed['api']['model'], 'keep-model')
            self.assertEqual(parsed['assistant']['system_prompt'], 'keep prompt')
            self.assertTrue(config.with_suffix('.toml.pre-tts-installer').is_file())

    def test_adds_missing_espeak_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.toml'
            config.write_text('[tts]\nengine = "edge"\n', encoding='utf-8')
            update_config({'engine': '"espeak_ng"', 'espeak_voice': '"ru+f3"'}, config)
            parsed = tomllib.loads(config.read_text(encoding='utf-8'))
            self.assertEqual(parsed['tts']['engine'], 'espeak_ng')
            self.assertEqual(parsed['tts']['espeak_voice'], 'ru+f3')


if __name__ == '__main__':
    unittest.main()
