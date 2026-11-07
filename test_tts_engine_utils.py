import unittest
from tts_engine_utils import espeak_command, espeak_settings, piper_settings


class EspeakSettingsTests(unittest.TestCase):
    def test_piper_applies_rate_and_volume(self):
        length_scale, volume = piper_settings('+5%', '+0%')
        self.assertAlmostEqual(length_scale, 1 / 1.05)
        self.assertEqual(volume, 1.0)
        self.assertEqual(piper_settings('-50%', '+200%'), (2.0, 2.0))
        self.assertEqual(piper_settings('+500%', '-200%'), (0.5, 0.0))

    def test_edge_style_percentage_maps_to_espeak(self):
        self.assertEqual(espeak_settings('+5%', '+0%'), (184, 100))

    def test_numeric_rate_is_words_per_minute(self):
        self.assertEqual(espeak_settings('190', '+20%'), (190, 120))

    def test_values_are_clamped(self):
        self.assertEqual(espeak_settings('-100%', '-200%'), (80, 0))
        self.assertEqual(espeak_settings('+500%', '+300%'), (450, 200))

    def test_command_is_argument_array_and_uses_stdin(self):
        command = espeak_command('/usr/bin/espeak-ng', 'ru+f3', '+5%', '+0%', '/tmp/a.wav')
        self.assertEqual(command, ['/usr/bin/espeak-ng', '-v', 'ru+f3', '-s', '184', '-a', '100', '-w', '/tmp/a.wav', '--stdin'])


if __name__ == '__main__':
    unittest.main()
