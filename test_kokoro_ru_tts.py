from __future__ import annotations

import unittest
import threading
from collections import OrderedDict

from kokoro_ru_tts import KokoroRuEngine


class KokoroRuTextTests(unittest.TestCase):
    def test_long_text_is_split_into_safe_segments(self):
        text = "Привет, Пура! " + ("Я проверяю скорость локальной озвучки. " * 30)
        parts = KokoroRuEngine._split_long_text(text, 145)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(0 < len(part) <= 145 for part in parts))
        self.assertEqual(" ".join(" ".join(parts).split()), " ".join(text.split()))

    def test_short_text_stays_a_single_segment(self):
        self.assertEqual(KokoroRuEngine._split_long_text("Привет, Пура!"), ["Привет, Пура!"])

    def test_empty_text_returns_no_segments(self):
        self.assertEqual(KokoroRuEngine._split_long_text(" \n \t "), [])

    def test_phoneme_cache_reuses_g2p_and_is_bounded(self):
        class FakeG2P:
            def __init__(self):
                self.calls = []
            def __call__(self, text):
                self.calls.append(text)
                return ("IPA:" + text, [])

        engine = object.__new__(KokoroRuEngine)
        engine._g2p_lock = threading.Lock()
        engine._phoneme_cache = OrderedDict()
        engine._phoneme_cache_limit = 2
        engine.g2p = FakeG2P()

        self.assertEqual(engine._phonemize("Привет  Пура."), ("IPA:Привет Пура.", False, False))
        self.assertEqual(engine._phonemize("Привет Пура."), ("IPA:Привет Пура.", False, True))
        self.assertEqual(len(engine.g2p.calls), 1)
        engine._phonemize("Второе предложение.")
        engine._phonemize("Третье предложение.")
        self.assertLessEqual(len(engine._phoneme_cache), 2)
        self.assertNotIn("Привет Пура.", engine._phoneme_cache)


if __name__ == "__main__":
    unittest.main()
