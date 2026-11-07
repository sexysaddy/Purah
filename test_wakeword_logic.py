import unittest
from wakeword_logic import build_wakeword_grammar, contains_wake_phrase, is_close_command, name_variants


class WakewordGrammarTests(unittest.TestCase):
    def test_idle_mode_requires_full_phrase_and_has_no_unknown_fallback(self):
        grammar = build_wakeword_grammar("Привет, Пура")
        self.assertIn("привет пура", grammar)
        self.assertIn("привет пора", grammar)  # common Vosk vowel confusion, still a full phrase
        self.assertNotIn("пура", grammar)
        self.assertNotIn("[unk]", grammar)
        self.assertNotIn("эй пура", grammar)

    def test_interrupt_mode_accepts_name_and_variants_plus_commands(self):
        grammar = build_wakeword_grammar("Привет, Пура", interrupt_mode=True)
        self.assertIn("привет пура", grammar)
        self.assertIn("пура", grammar)
        self.assertIn("пурочка", grammar)
        self.assertIn("пурушка", grammar)
        self.assertIn("пура стоп", grammar)
        self.assertIn("пура слушай", grammar)
        self.assertNotIn("[unk]", grammar)

    def test_custom_phrase_preserved_and_normalized(self):
        grammar = build_wakeword_grammar("Эй, Пура", interrupt_mode=True)
        self.assertIn("эй пура", grammar)
        self.assertIn("эй пора", grammar)
        self.assertIn("пурочка", grammar)
        self.assertEqual(len(grammar), len(set(grammar)))

    def test_single_name_does_not_match_idle_phrase(self):
        grammar = build_wakeword_grammar("Привет, Пура")
        self.assertFalse(contains_wake_phrase("пура", grammar))
        self.assertFalse(contains_wake_phrase("вот какая-то фраза", grammar))
        self.assertTrue(contains_wake_phrase("да, привет, Пура, посмотри на экран", grammar))
        self.assertTrue(contains_wake_phrase("привет пора", grammar))

    def test_interrupt_commands_must_be_complete(self):
        grammar = build_wakeword_grammar("Привет, Пура", interrupt_mode=True)
        self.assertTrue(contains_wake_phrase("пура стоп", grammar))
        self.assertTrue(contains_wake_phrase("пура слушай", grammar))
        self.assertTrue(contains_wake_phrase("пурочка", grammar))
        self.assertFalse(contains_wake_phrase("пора", grammar))  # Vosk confusion must not barge in


class FollowupAndCloseTests(unittest.TestCase):
    def test_followup_mode_accepts_name_and_variants_in_short_window(self):
        followup = build_wakeword_grammar("Привет, Пура", followup_mode=True)
        idle = build_wakeword_grammar("Привет, Пура", followup_mode=False)
        for variant in ("пура", "пурочка", "пурушка", "пуруся"):
            self.assertIn(variant, followup)
            self.assertNotIn(variant, idle)

    def test_close_commands_are_local_and_narrow(self):
        for text in ("пура закройся", "пурочка закройся", "привет пура закройся", "ПУРА, закрой окно пожалуйста"):
            self.assertTrue(is_close_command(text), text)
        for text in ("закрой терминал", "пура расскажи про закрытие", "пора закройся"):
            self.assertFalse(is_close_command(text), text)

    def test_custom_name_is_respected(self):
        self.assertTrue(is_close_command("эй пура закройся", "Эй, Пура"))
        self.assertFalse(is_close_command("привет пура закройся", "Эй, Пура"))

    def test_name_variants_generated(self):
        variants = set(name_variants("пура"))
        for expected in ("пура", "пурочка", "пурушка", "пуронька", "пурунька", "пуречка", "пурулька", "пуруся"):
            self.assertIn(expected, variants)


if __name__ == "__main__":
    unittest.main()