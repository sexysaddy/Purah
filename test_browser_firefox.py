"""Headless WebDriver BiDi protocol tests; no Firefox or display server required."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from purah_agent.browser import BrowserConfigurationError, browser_automation_enabled, configured_browser
from purah_agent.browser_bidi import BrowserBidi, BrowserBidiError
from purah_agent.browser_cdp import BrowserCdp
from purah_agent.browser_common import BrowserNavigationError, external_http_url


class FakeWebSocket:
    def __init__(self, *, fail_method=""):
        self.commands = []
        self.closed = False
        self.fail_method = fail_method
        self.messages = []

    def settimeout(self, seconds):
        assert seconds > 0

    def send(self, payload):
        command = json.loads(payload)
        self.commands.append(command)
        ident, method = command["id"], command["method"]
        if method == self.fail_method:
            self.messages.append({"id": ident, "type": "error", "error": "session not created", "message": "Maximum active sessions"})
            return
        if method == "session.new":
            result = {"sessionId": "fake-automation-session", "capabilities": {"browserName": "firefox"}}
        elif method == "browsingContext.getTree":
            result = {"contexts": [
                {"context": "tab-1", "url": "https://example.org/posts", "children": []},
                {"context": "tab-2", "url": "https://example.org/other", "children": []},
                {"context": "start", "url": "about:home", "children": []},
                {"context": "internal", "url": "about:preferences", "children": []},
            ]}
        elif method == "script.evaluate":
            result = {"type": "success", "result": {"type": "string", "value": json.dumps({
                "title": "Новости", "text": "Первый пост: тест", 
            }, ensure_ascii=False)}}
        elif method == "browsingContext.navigate":
            result = {"navigation": "nav-1", "url": command["params"]["url"]}
        elif method == "session.end":
            result = {}
        else:
            raise AssertionError(f"Unexpected WebDriver BiDi command: {method}")
        # Events are allowed before the response, and must be ignored.
        self.messages.append({"type": "event", "method": "log.entryAdded", "params": {}})
        self.messages.append({"id": ident, "type": "success", "result": result})

    def recv(self):
        if not self.messages:
            raise AssertionError("Driver read after receiving all replies")
        return json.dumps(self.messages.pop(0), ensure_ascii=False)

    def close(self):
        self.closed = True


class FirefoxBiDiTests(unittest.TestCase):
    def setUp(self):
        self.sockets = []
        self.urls = []

        def connect(url):
            self.urls.append(url)
            sock = FakeWebSocket()
            self.sockets.append(sock)
            return sock

        self.adapter = BrowserBidi(port=9223, connect=connect)

    def test_list_tabs_protocol_and_session_cleanup(self):
        result = self.adapter.list_tabs()
        self.assertEqual(result["backend"], "firefox-bidi")
        self.assertEqual([tab["tab_id"] for tab in result["tabs"]], ["tab-1", "tab-2", "start"])
        self.assertEqual(self.urls, ["ws://127.0.0.1:9223/session"])
        self.assertTrue(self.sockets[0].closed)
        self.assertEqual([c["method"] for c in self.sockets[0].commands],
                         ["session.new", "browsingContext.getTree", "session.end"])
        self.assertEqual(self.sockets[0].commands[0]["params"]["capabilities"]["alwaysMatch"]["browserName"], "firefox")

    def test_read_selected_tab_and_never_execute_model_js(self):
        result = self.adapter.read_tab("tab-1")
        self.assertEqual(result["title"], "Новости")
        self.assertIn("Первый пост", result["text"])
        commands = self.sockets[0].commands
        self.assertEqual([c["method"] for c in commands],
                         ["session.new", "browsingContext.getTree", "script.evaluate", "session.end"])
        self.assertIn("document.body.innerText", commands[2]["params"]["expression"])
        self.assertEqual(commands[2]["params"]["target"], {"context": "tab-1"})
        self.assertFalse(commands[2]["params"]["awaitPromise"])
        self.assertEqual(commands[2]["params"]["resultOwnership"], "none")
        with self.assertRaises(BrowserBidiError):
            self.adapter.read_tab("missing")
        with self.assertRaises(BrowserBidiError):
            self.adapter.read_tab("start")
        self.assertEqual([c["method"] for c in self.sockets[1].commands],
                         ["session.new", "browsingContext.getTree", "session.end"])

    def test_navigation_rejects_private_before_connect(self):
        for url in ("file:///etc/passwd", "https://127.0.0.1/private", "http://localhost/admin",
                    "https://[::1]/", "https://alice:password@example.org/", "http://foo.internal/",
                    "http://192.168.1.1", "http://example.org:99999/news", "ftp://example.org"):
            with self.subTest(url=url), self.assertRaises(BrowserNavigationError):
                self.adapter.navigate_tab("tab-1", url)
        self.assertFalse(self.sockets)
        response = self.adapter.navigate_tab("start", "https://example.org/latest")
        self.assertTrue(response["navigation_started"])
        methods = self.sockets[0].commands
        self.assertEqual(methods[2]["method"], "browsingContext.navigate")
        self.assertEqual(methods[2]["params"]["wait"], "none")
        self.assertEqual(methods[2]["params"]["context"], "start")
        self.assertTrue(self.sockets[0].closed)

    def test_fails_closed_if_browser_cannot_start_bidi_session(self):
        socket = FakeWebSocket(fail_method="session.new")
        adapter = BrowserBidi(connect=lambda url: socket)
        with self.assertRaisesRegex(BrowserBidiError, "session.new"):
            adapter.list_tabs()
        self.assertTrue(socket.closed)
        self.assertEqual([c["method"] for c in socket.commands], ["session.new"])

    def test_websocket_endpoint_is_loopback_and_cannot_be_supplied_by_model(self):
        adapter = BrowserBidi(port=9223, connect=lambda url: self.fail_if_remote(url))
        adapter.list_tabs()
        with self.assertRaises(ValueError):
            BrowserBidi(port=22)
        with self.assertRaises(ValueError):
            BrowserBidi(port=65536)

    def fail_if_remote(self, url):
        self.assertEqual(url, "ws://127.0.0.1:9223/session")
        return FakeWebSocket()

    def test_automation_routing_disabled_by_default_and_backwards_compatible(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(browser_automation_enabled())
            with self.assertRaises(BrowserConfigurationError):
                configured_browser()
        with patch.dict(os.environ, {"PURAH_BROWSER_CDP_ENABLED": "true"}, clear=True):
            self.assertIsInstance(configured_browser(), BrowserCdp)
        with patch.dict(os.environ, {"PURAH_BROWSER_FIREFOX_BIDI_ENABLED": "true"}, clear=True):
            self.assertIsInstance(configured_browser(), BrowserBidi)
        with patch.dict(os.environ, {
            "PURAH_DEFAULT_BROWSER": "firefox", "PURAH_BROWSER_CDP_ENABLED": "true",
            "PURAH_BROWSER_FIREFOX_BIDI_ENABLED": "true",
        }, clear=True):
            self.assertIsInstance(configured_browser(), BrowserBidi)
        with patch.dict(os.environ, {
            "PURAH_DEFAULT_BROWSER": "firefox", "PURAH_BROWSER_CDP_ENABLED": "true",
            "PURAH_BROWSER_FIREFOX_BIDI_ENABLED": "true",
            "PURAH_BROWSER_AUTOMATION_BACKEND": "chromium",
        }, clear=True):
            self.assertIsInstance(configured_browser(), BrowserCdp)
        with patch.dict(os.environ, {
            "PURAH_BROWSER_AUTOMATION_BACKEND": "firefox",
            "PURAH_BROWSER_CDP_ENABLED": "true",
        }, clear=True):
            with self.assertRaisesRegex(BrowserConfigurationError, "Firefox"):
                configured_browser()
        with patch.dict(os.environ, {"PURAH_BROWSER_AUTOMATION_BACKEND": "script"}, clear=True):
            with self.assertRaises(BrowserConfigurationError):
                configured_browser()

    def test_navigation_shared_guard_for_both_backends(self):
        self.assertEqual(external_http_url(" https://example.org/a "), "https://example.org/a")
        with self.assertRaises(BrowserNavigationError):
            external_http_url("http://127.0.0.1/endpoint")


if __name__ == "__main__":
    unittest.main()
