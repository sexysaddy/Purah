from __future__ import annotations

import ast
import os
import unittest
from unittest import mock
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _auth_helpers():
    names = {"_has_real_api_key", "_auth_headers"}
    nodes = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {
        "os": os,
        "urlparse": urlparse,
        "_AUTH_KEY_NOT_SUPPLIED": object(),
        "_API_KEY_PLACEHOLDERS": {
            "replace_me", "REPLACE_WITH_FRESH_API_KEY", "YOUR_API_KEY", "ВАШ_API_KEY",
            "PUT_YOUR_API_KEY_HERE", "PUT_YOUR_DEEPSEEK_API_KEY_HERE",
            "PUT_YOUR_GROQ_API_KEY_HERE", "PUT_YOUR_VISION_API_KEY_HERE", "PASTE_API_KEY_HERE",
        },
        "CONFIG_AUTHORITATIVE_ENV": {"OPENAI_API_KEY"},
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / "main.py"), "exec"), namespace)
    return namespace


class AuthKeyIsolationTests(unittest.TestCase):
    def test_empty_vision_key_does_not_borrow_valid_deepseek_chat_key(self):
        ns = _auth_helpers()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "valid-deepseek-key"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, r"\[vision\]\.api_key"):
                ns["_auth_headers"](
                    "https://api.groq.com/openai/v1/chat/completions",
                    key_override="",
                    key_setting="[vision].api_key",
                )

    def test_empty_stt_key_does_not_borrow_valid_deepseek_chat_key(self):
        ns = _auth_helpers()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "valid-deepseek-key"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, r"\[speech\]\.stt_api_key"):
                ns["_auth_headers"](
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    key_override="",
                    key_setting="[speech].stt_api_key",
                )

    def test_text_route_still_uses_configured_chat_key(self):
        ns = _auth_helpers()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "valid-deepseek-key"}, clear=True):
            headers = ns["_auth_headers"]("https://api.deepseek.com/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer valid-deepseek-key")

    def test_stt_and_vision_explicitly_pass_their_own_keys(self):
        self.assertIn('key_setting="[speech].stt_api_key"', SOURCE)
        self.assertIn('else "[vision].api_key"', SOURCE)
        self.assertIn('key_override=request_key', SOURCE)
        self.assertIn('key_override=request_key', SOURCE)


if __name__ == "__main__":
    unittest.main()
