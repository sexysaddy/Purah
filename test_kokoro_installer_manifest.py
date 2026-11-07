from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent

class KokoroInstallerManifestTests(unittest.TestCase):
    def test_installer_downloads_both_configs(self):
        script = (ROOT / "setup_kokoro_ru.sh").read_text(encoding="utf-8")
        self.assertIn("\"config.json\"", script)
        self.assertIn("\"kokoro-config.json\"", script)
        self.assertIn('root / "kokoro-config.json": 2_000', script)

    def test_runtime_checks_g2p_config_before_loading(self):
        source = (ROOT / "kokoro_ru_tts.py").read_text(encoding="utf-8")
        self.assertIn('g2p_config_path = self.model_dir / "kokoro-config.json"', source)
        self.assertIn("model_path, config_path, g2p_config_path, g2p_path, voice_path", source)

    def test_installer_has_explicit_config_recovery_and_version_banner(self):
        script = (ROOT / "setup_kokoro_ru.sh").read_text(encoding="utf-8")
        self.assertIn("PURAH Kokoro installer 1.1.3", script)
        self.assertIn("def valid_g2p_config", script)
        self.assertIn("https://huggingface.co/{repo}/resolve/{revision}/kokoro-config.json?download=true", script)
        self.assertIn("config_data['vocab']", script)
        self.assertIn("len(vocab) > 50", script)

    def test_tts_diagnostic_plays_synthesized_audio(self):
        source = (ROOT / "test_tts_playback.py").read_text(encoding="utf-8")
        self.assertIn("TTSStreamWorker", source)
        self.assertIn("QMediaPlayer", source)
        self.assertIn("--kokoro", source)

if __name__ == "__main__":
    unittest.main()
