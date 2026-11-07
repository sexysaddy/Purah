from __future__ import annotations

import unittest

from provider_config import resolve_vision_route, same_provider_origin


class ProviderRoutingTests(unittest.TestCase):
    def test_vision_uses_its_own_endpoint_model_and_key_by_default(self):
        route = resolve_vision_route(
            inherit_api=False,
            vision_endpoint="https://api.groq.com/openai/v1/chat/completions",
            vision_model="qwen/qwen3.8-27b",
            vision_api_key="groq-vision-key",
            chat_endpoint="https://api.deepseek.com/chat/completions",
            chat_model="deepseek-flash",
            chat_api_key="deepseek-chat-key",
        )
        self.assertEqual(route.endpoint, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(route.model, "qwen/qwen3.8-27b")
        self.assertEqual(route.api_key, "groq-vision-key")
        self.assertFalse(route.inherited_chat_api)

    def test_chat_api_is_used_only_after_explicit_opt_in(self):
        route = resolve_vision_route(
            inherit_api=True,
            vision_endpoint="",
            vision_model="",
            vision_api_key="",
            chat_endpoint="https://api.deepseek.com/chat/completions",
            chat_model="deepseek-flash",
            chat_api_key="deepseek-chat-key",
        )
        self.assertEqual(route.endpoint, "https://api.deepseek.com/chat/completions")
        self.assertEqual(route.model, "deepseek-flash")
        self.assertEqual(route.api_key, "deepseek-chat-key")
        self.assertTrue(route.inherited_chat_api)

    def test_inheritance_does_not_send_chat_key_to_a_different_vision_host(self):
        route = resolve_vision_route(
            inherit_api=True,
            vision_endpoint="https://api.groq.com/openai/v1/chat/completions",
            vision_model="qwen/qwen3.8-27b",
            vision_api_key="",
            chat_endpoint="https://api.deepseek.com/chat/completions",
            chat_model="deepseek-flash",
            chat_api_key="deepseek-chat-key",
        )
        self.assertEqual(route.endpoint, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(route.model, "qwen/qwen3.8-27b")
        self.assertEqual(route.api_key, "", "unrelated provider must not receive the chat key")

    def test_explicit_vision_key_overrides_chat_key_when_inheritance_is_enabled(self):
        route = resolve_vision_route(
            inherit_api=True,
            vision_endpoint="https://vision.example/v1/chat/completions",
            vision_model="vision-model",
            vision_api_key="vision-key",
            chat_endpoint="https://chat.example/v1/chat/completions",
            chat_model="chat-model",
            chat_api_key="chat-key",
        )
        self.assertEqual(route.endpoint, "https://vision.example/v1/chat/completions")
        self.assertEqual(route.model, "vision-model")
        self.assertEqual(route.api_key, "vision-key")

    def test_missing_separate_endpoint_or_model_fails_locally_instead_of_calling_wrong_api(self):
        shared = dict(
            inherit_api=False,
            vision_endpoint="",
            vision_model="",
            vision_api_key="",
            chat_endpoint="https://api.deepseek.com/chat/completions",
            chat_model="deepseek-flash",
            chat_api_key="valid-chat-key",
        )
        with self.assertRaisesRegex(ValueError, r"\[vision\]\.chat_url"):
            resolve_vision_route(**shared)
        with self.assertRaisesRegex(ValueError, r"\[vision\]\.model"):
            resolve_vision_route(**(shared | {
                "vision_endpoint": "https://api.groq.com/openai/v1/chat/completions"
            }))

    def test_same_provider_origin_requires_same_scheme_host_and_port(self):
        self.assertTrue(same_provider_origin(
            "https://api.deepseek.com/chat/completions",
            "https://api.deepseek.com/v1/audio/transcriptions",
        ))
        self.assertFalse(same_provider_origin(
            "https://api.deepseek.com/chat/completions",
            "https://api.groq.com/openai/v1/chat/completions",
        ))
        self.assertFalse(same_provider_origin(
            "https://localhost:1234/v1/chat/completions",
            "http://localhost:1234/v1/chat/completions",
        ))
        self.assertFalse(same_provider_origin(
            "http://127.0.0.1:1234/v1/chat/completions",
            "http://127.0.0.1:5678/v1/chat/completions",
        ))

    def test_inheritance_does_not_share_chat_key_when_port_differs(self):
        route = resolve_vision_route(
            inherit_api=True,
            vision_endpoint="https://api.example.com:8443/openai/v1/chat/completions",
            vision_model="vision-model",
            vision_api_key="",
            chat_endpoint="https://api.example.com/v1/chat/completions",
            chat_model="chat-model",
            chat_api_key="chat-key",
        )
        self.assertEqual(route.api_key, "", "different ports can represent different services")


if __name__ == "__main__":
    unittest.main()
