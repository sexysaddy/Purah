import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class TTSRuntimeContractTests(unittest.TestCase):
    def test_kokoro_is_only_imported_from_selected_runtime_branches(self):
        source = (ROOT / 'purah_agent/tts_worker.py').read_text(encoding='utf-8')
        imports = [line for line in source.splitlines() if 'from kokoro_ru_tts import' in line]
        self.assertTrue(imports)
        for line in imports:
            self.assertTrue(line.lstrip().startswith('from kokoro_ru_tts import'), line)
        # Import must be inside run(), not at module level.
        import ast
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(node, ast.ImportFrom) and node.module == "kokoro_ru_tts" for node in tree.body))
        self.assertIn('if tts_enabled and tts_engine == "kokoro_ru" and warmup_enabled:', (ROOT / 'main.py').read_text(encoding='utf-8'))
        self.assertIn('if self.engine == "kokoro_ru":', source)
        self.assertIn('kokoro_engine = get_kokoro_ru_engine(', source)

    def test_toml_is_authoritative_for_all_explicit_settings(self):
        source = (ROOT / 'main.py').read_text(encoding='utf-8')
        self.assertIn('CONFIG_AUTHORITATIVE_ENV', source)
        self.assertIn('config.toml/defaults override inherited', source)
        self.assertIn('os.environ[env_name] = value', source)
        self.assertNotIn('PURAH_TTS_ENV_OVERRIDE', source)

    def test_full_system_access_and_sudo_askpass_are_wired(self):
        source = (ROOT / 'main.py').read_text(encoding='utf-8')
        config = (ROOT / 'config.toml.example').read_text(encoding='utf-8')
        self.assertIn('PURAH_TERMINAL_FULL_ACCESS', source)
        self.assertIn('sudo -A COMMAND', (ROOT / 'purah_agent/tool_specs.py').read_text(encoding='utf-8'))
        self.assertIn('full_access = true', config)
        self.assertIn('allow_sudo = true', config)
        self.assertTrue((ROOT / 'sudo_askpass.py').is_file())

    def test_old_version_cleanup_precedes_single_instance_ipc_forwarding(self):
        source = (ROOT / 'main.py').read_text(encoding='utf-8')
        main_body = source[source.index('def main() -> None:'):]
        cleanup_pos = main_body.index('_cleanup_old_instances()')
        ipc_pos = main_body.index('_send_to_existing_instance(command)')
        self.assertLess(cleanup_pos, ipc_pos)
        self.assertIn('record.get("config_sha256") == _config_fingerprint()', source)
        self.assertIn('"config_sha256": _config_fingerprint()', source)


    def test_config_toml_is_single_source_of_truth_including_missing_fields(self):
        source = (ROOT / 'main.py').read_text(encoding='utf-8')
        self.assertIn('by filling missing fields from the shipped example', source)
        self.assertIn('if (_user_config_dir() / "config.toml").is_file():', source)
        self.assertIn('os.environ.pop("PURAH_API_KEY", None)', source)
        template = (ROOT / 'config.toml.example').read_text(encoding='utf-8')
        self.assertIn('max_tool_rounds = 12', template)
        self.assertIn('end_silence_seconds = 1.15', template)
        self.assertIn('max_record_seconds = 45', template)

    def test_full_access_extends_file_tools_beyond_project_with_local_backups(self):
        source = (ROOT / 'main.py').read_text(encoding='utf-8')
        self.assertIn('full_access = os.getenv("PURAH_TERMINAL_FULL_ACCESS"', source)
        self.assertIn('(_user_data_dir() / "file_backups")', source)
        self.assertIn('if not _is_inside_project(target) and not full_access:', source)
        self.assertIn('sudo -A <команда>', source)

    def test_espeak_ng_is_a_supported_engine(self):
        source = (ROOT / 'purah_agent/tts_worker.py').read_text(encoding='utf-8')
        self.assertIn('{"edge", "piper", "espeak_ng", "kokoro_ru"}', source)
        self.assertIn('elif self.engine == "espeak_ng":', source)
        self.assertIn('espeak_command(', source)


if __name__ == '__main__':
    unittest.main()
