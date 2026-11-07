import asyncio
import unittest
from types import SimpleNamespace

from purah_agent.telegram import TelegramConnector


class FakeClient:
    def __init__(self):
        self.sent = []
        self.messages = {}
        self.dialogs = [
            SimpleNamespace(entity=SimpleNamespace(id=1, first_name="Иван", last_name="Петров", username="ivan"), name="Иван Петров"),
            SimpleNamespace(entity=SimpleNamespace(id=2, title="Новости-москва", broadcast=True), name="Новости-москва"),
            SimpleNamespace(entity=SimpleNamespace(id=3, title="Новости-питер", broadcast=True), name="Новости-питер"),
            SimpleNamespace(entity=SimpleNamespace(id=4, first_name="Мама", last_name="", username="mama"), name="Мама"),
        ]

    def iter_dialogs(self):
        return self.dialogs

    async def send_message(self, entity, text):
        self.sent.append((getattr(entity, "id", entity), text))
        return SimpleNamespace(id=99)

    async def get_messages(self, entity, limit=1):
        return [self.messages.get(entity.id, SimpleNamespace(id=8, message="Последний пост"))]


class AsyncDialogClient(FakeClient):
    async def _dialogs(self):
        for dialog in self.dialogs:
            yield dialog

    def iter_dialogs(self):
        return self._dialogs()


class TelegramConnectorTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClient()
        self.connector = TelegramConnector(client=self.fake, resolution_ttl=60)

    def tearDown(self):
        self.connector.close()

    def test_exact_single_match_can_be_sent_without_mouse_or_gui(self):
        result = self.connector.resolve_recipient("мама")
        self.assertEqual(result["status"], "unique_exact")
        candidate_id = result["candidates"][0]["candidate_id"]
        sent = self.connector.send_message(candidate_id, "Привет")
        self.assertTrue(sent["ok"])
        self.assertEqual(self.fake.sent, [(4, "Привет")])

    def test_declension_is_fuzzy_and_requires_confirmation(self):
        result = self.connector.resolve_recipient("ивану петрову")
        self.assertEqual(result["status"], "confirmation_required")
        self.assertTrue(result["requires_confirmation"])
        candidate_id = result["candidates"][0]["candidate_id"]
        refused = self.connector.send_message(candidate_id, "Петров")
        self.assertEqual(refused["error"], "recipient_confirmation_required")
        sent = self.connector.send_message(candidate_id, "Петров", confirmed=True)
        self.assertTrue(sent["ok"])

    def test_similar_channel_names_are_ambiguous(self):
        result = self.connector.resolve_recipient("Новости")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 2)
        refused = self.connector.send_message(result["candidates"][0]["candidate_id"], "Текст")
        self.assertEqual(refused["error"], "recipient_confirmation_required")

    def test_timeout_or_second_attempt_never_retries_silently(self):
        result = self.connector.resolve_recipient("мама")
        candidate_id = result["candidates"][0]["candidate_id"]
        first = self.connector.send_message(candidate_id, "Один раз")
        self.assertTrue(first["ok"])
        second = self.connector.send_message(candidate_id, "Повтор")
        self.assertEqual(second["error"], "send_already_attempted")

    def test_latest_message_is_bounded_and_text_only(self):
        result = self.connector.resolve_recipient("Новости-москва")
        candidate_id = result["candidates"][0]["candidate_id"]
        latest = self.connector.latest_message(candidate_id)
        self.assertTrue(latest["ok"])
        self.assertEqual(latest["text"], "Последний пост")

    def test_saved_messages_uses_special_peer(self):
        result = self.connector.resolve_recipient("Избранное")
        self.assertEqual(result["status"], "unique_exact")
        candidate_id = result["candidates"][0]["candidate_id"]
        sent = self.connector.send_message(candidate_id, "Привет")
        self.assertTrue(sent["ok"])
        self.assertEqual(self.fake.sent, [("me", "Привет")])

    def test_async_dialog_iterator_is_supported(self):
        connector = TelegramConnector(client=AsyncDialogClient(), resolution_ttl=60)
        try:
            result = connector.resolve_recipient("мама")
            self.assertEqual(result["status"], "unique_exact")
        finally:
            connector.close()


if __name__ == "__main__":
    unittest.main()

