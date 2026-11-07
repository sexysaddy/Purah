"""Safety/regression checks for the 1.4.1 browser and terminal cleanup.

No real Firefox, Chromium, tmux, terminal emulator or compositor is required.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from purah_agent.browser_bidi import BrowserBidi, BrowserBidiError
from purah_agent.browser_cdp import BrowserCdp, BrowserCdpError
from purah_terminal import TerminalManager


class LocalTabsSocket:
    def __init__(self):
        self.messages = []
        self.closed = False

    def settimeout(self, value):
        pass

    def send(self, data):
        sent = json.loads(data)
        method = sent["method"]
        if method == "session.new":
            response = {"sessionId": "session-id"}
        elif method == "browsingContext.getTree":
            response = {"contexts": [
                {"context": "public", "url": "https://example.org/"},
                {"context": "private", "url": "http://localhost:9001/private"},
                {"context": "secret", "url": "https://user:password@example.org/"},
                {"context": "start", "url": "about:home"},
            ]}
        elif method == "session.end":
            response = {}
        else:
            raise AssertionError(method)
        self.messages.append(json.dumps({"id": sent["id"], "type": "success", "result": response}))

    def recv(self):
        return self.messages.pop(0)

    def close(self):
        self.closed = True


class ReleaseIntegrityTests(unittest.TestCase):
    def test_firefox_does_not_expose_local_or_credentialed_tabs(self):
        sockets = []
        def connect(url):
            self.assertEqual(url, "ws://127.0.0.1:9223/session")
            socket = LocalTabsSocket()
            sockets.append(socket)
            return socket
        browser = BrowserBidi(connect=connect)
        tabs = browser.list_tabs()["tabs"]
        self.assertEqual([tab["tab_id"] for tab in tabs], ["public", "start"])
        with self.assertRaises(BrowserBidiError):
            browser.read_tab("private")
        self.assertTrue(all(socket.closed for socket in sockets))

    def test_cdp_exposes_only_public_tabs_or_navigation_start_page(self):
        targets = [
            {"id": "external", "type": "page", "url": "https://example.org/"},
            {"id": "local", "type": "page", "url": "http://127.0.0.1:9001/"},
            {"id": "secret", "type": "page", "url": "https://x:y@example.org/"},
            {"id": "start", "type": "page", "url": "chrome://newtab/"},
        ]
        browser = BrowserCdp(fetch=lambda _path: targets)
        self.assertEqual([tab["tab_id"] for tab in browser.list_tabs()["tabs"]],
                         ["external", "start"])
        with self.assertRaises(BrowserCdpError):
            browser.read_tab("local")

    def test_cdp_limits_websocket_response_memory(self):
        class HugeSocket:
            def settimeout(self, _seconds):
                pass
            def send(self, _payload):
                pass
            def recv(self):
                return "a" * 262145
            def close(self):
                self.closed = True
        socket = HugeSocket()
        target = [{"type": "page", "url": "https://example.org", "id": "ok",
                   "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/ok"}]
        browser = BrowserCdp(fetch=lambda _path: target, connect=lambda _url: socket)
        with self.assertRaisesRegex(BrowserCdpError, "256 KiB"):
            browser.read_tab("ok")
        self.assertTrue(socket.closed)

    def test_terminal_uses_own_tmux_socket_and_no_provider_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = TerminalManager(Path(tmp), state_dir=Path(tmp))
            sequence = iter([False, True])
            manager._session_exists = lambda _session: next(sequence)
            captured = {}
            def fake_popen(argv, **kwargs):
                captured.update({"argv": argv, "kwargs": kwargs})
                return type("Proc", (), {"pid": 1234})()
            manager._popen = fake_popen
            with patch.dict(os.environ, {
                "PURAH_VISIBLE_TERMINAL_RESTORE_FOCUS": "false",
                "OPENAI_API_KEY": "secret-should-not-leak",
                "PURAH_STT_API_KEY": "another-secret",
                "PURAH_TERMINAL_APP": "gnome-terminal",
            }), patch("shutil.which", side_effect=lambda name: "/usr/bin/" + name):
                manager._start_visible_session("purah-agent", Path(tmp))
            self.assertEqual(captured["argv"][:4],
                             ["/usr/bin/gnome-terminal", "--", "tmux", "-L"])
            self.assertEqual(captured["argv"][4], "purah")
            self.assertNotIn("OPENAI_API_KEY", captured["kwargs"]["env"])
            self.assertNotIn("PURAH_STT_API_KEY", captured["kwargs"]["env"])

    def test_visible_terminal_refuses_busy_pane_without_typing(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = TerminalManager(Path(tmp), state_dir=Path(tmp))
            manager._start_visible_session = lambda _session, _cwd: False
            sent = []
            def fake_tmux(argv):
                sent.append(argv)
                return subprocess.CompletedProcess(argv, 0, "vim\n", "")
            manager._tmux = fake_tmux
            with self.assertRaisesRegex(RuntimeError, "busy"):
                manager.run_visible("echo hello")
            self.assertEqual([item[0] for item in sent], ["display-message"])

    def test_visible_terminal_aborts_failed_paste_before_enter(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = TerminalManager(Path(tmp), state_dir=Path(tmp))
            manager._start_visible_session = lambda _session, _cwd: False
            sent = []
            def fake_tmux(argv):
                sent.append(argv)
                if argv[0] == "display-message":
                    return subprocess.CompletedProcess(argv, 0, "bash\n", "")
                return subprocess.CompletedProcess(argv, 1, "", "paste failed")
            manager._tmux = fake_tmux
            with self.assertRaisesRegex(RuntimeError, "could not write"):
                manager.run_visible("echo hello")
            self.assertEqual([item[0] for item in sent], ["display-message", "send-keys"])


if __name__ == "__main__":
    unittest.main()
