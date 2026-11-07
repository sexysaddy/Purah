from __future__ import annotations
import unittest
from tts_text import clean_tts_text, extract_tts_segments


class TTSTextTests(unittest.TestCase):
    def test_sentence_is_released_as_soon_as_complete(self):
        ready, remainder = extract_tts_segments("Привет, я Пура. ", final=False)
        self.assertEqual(ready, ["Привет, я Пура."])
        self.assertEqual(remainder, "")

    def test_unfinished_sentence_is_buffered(self):
        ready, remainder = extract_tts_segments("Привет, я Пура и сейчас", final=False)
        self.assertEqual(ready, [])
        self.assertEqual(remainder, "Привет, я Пура и сейчас")

    def test_two_sentences_are_split_in_order(self):
        ready, remainder = extract_tts_segments("Первое предложение. Второе предложение!", final=True)
        self.assertEqual(ready, ["Первое предложение.", "Второе предложение!"])
        self.assertEqual(remainder, "")

    def test_nonstreaming_final_text_is_split(self):
        ready, remainder = extract_tts_segments("Короткий ответ. И ещё один.", final=True)
        self.assertEqual(ready, ["Короткий ответ.", "И ещё один."])
        self.assertEqual(remainder, "")

    def test_long_sentence_is_chunked_near_natural_boundary(self):
        text = ("Это длинная фраза, которую нужно озвучить раньше, чем модель закончит весь ответ, " * 5).strip()
        ready, remainder = extract_tts_segments(text, final=False, max_chars=100)
        self.assertTrue(ready)
        self.assertTrue(all(len(item) <= 120 for item in ready))
        self.assertTrue(remainder or ready)

    def test_markdown_is_cleaned_for_speech(self):
        cleaned = clean_tts_text("**Привет** [ссылка](https://example.com) https://example.org")
        self.assertEqual(cleaned, "Привет ссылка ссылка")

    def test_abbreviation_does_not_force_early_cut(self):
        ready, remainder = extract_tts_segments("Это нужно т.е. уточнить", final=False)
        self.assertEqual(ready, [])
        self.assertEqual(remainder, "Это нужно т.е. уточнить")


if __name__ == "__main__":
    unittest.main()
