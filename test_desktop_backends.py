"""Headless end-to-end tests for PURAH's optional native backend contracts."""
from __future__ import annotations
import json
import os
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from purah_agent.accessibility import AccessibilityBackend
from purah_agent.browser_cdp import BrowserCdp, BrowserCdpError
from purah_agent.editor import symbols, search
from purah_agent.file_manager import reveal
from purah_agent import tool_journal
from purah_terminal import TerminalManager


class FakeAction:
    def __init__(self):
        self.actions = ["click"]
        self.pressed = 0
    def get_n_actions(self): return len(self.actions)
    def get_name(self, index): return self.actions[index]
    def do_action(self, index):
        self.pressed += 1
        return index == 0


class FakeNode:
    def __init__(self, name, pid=0, children=(), action=None, role=""):
        self.name = name
        self.pid = pid
        self.children = list(children)
        self.action = action
        self.role = role
    def get_child_count(self): return len(self.children)
    def get_child_at_index(self, n): return self.children[n]
    def get_process_id(self): return self.pid
    def get_name(self): return self.name
    def get_role_name(self): return self.role or ("push button" if self.action else "application")
    def get_description(self): return ""
    def get_action(self): return self.action
    def get_text(self): return None


class FakeAtspi:
    def __init__(self, root): self.root = root
    def get_desktop(self, n): return self.root


class DesktopBackendTests(unittest.TestCase):
    def test_atspi_uses_exact_pid_and_short_lived_snapshot(self):
        clock = [0.0]
        button = FakeAction()
        root = FakeNode("desktop", children=[FakeNode("My app", pid=222, children=[FakeNode("Save", action=button)])])
        adapter = AccessibilityBackend(FakeAtspi(root), clock=lambda: clock[0])
        tree = adapter.inspect(222)
        self.assertTrue(tree["ok"])
        node = next(n for n in tree["nodes"] if n["name"] == "Save")
        self.assertEqual(node["actions"], ["click"])
        self.assertFalse(adapter.act(223, tree["snapshot_id"], node["node_id"], "click")["ok"])
        self.assertEqual(button.pressed, 0)
        self.assertFalse(adapter.act(222, tree["snapshot_id"], node["node_id"], "delete")["ok"])
        self.assertEqual(button.pressed, 0)
        self.assertTrue(adapter.act(222, tree["snapshot_id"], node["node_id"], "click")["ok"])
        self.assertEqual(button.pressed, 1)
        # A UI mutation invalidates all node handles of that snapshot.
        self.assertFalse(adapter.act(222, tree["snapshot_id"], node["node_id"], "click")["ok"])
        clock[0] = 100
        again = adapter.inspect(222)
        clock[0] = 140
        self.assertFalse(adapter.act(222, again["snapshot_id"], node["node_id"], "click")["ok"])

    def test_atspi_ambiguous_pid_is_not_guessed(self):
        root = FakeNode("desktop", children=[FakeNode("one", pid=22), FakeNode("two", pid=22)])
        with self.assertRaisesRegex(Exception, "найдено 2"):
            AccessibilityBackend(FakeAtspi(root)).inspect(22)

    def test_atspi_disambiguates_windows_and_binds_actions_to_address(self):
        one = FakeAction()
        two = FakeAction()
        frames = [FakeNode("Tab A", children=[FakeNode("Save A", action=one)], role="frame"),
                  FakeNode("Tab B", children=[FakeNode("Save B", action=two)], role="frame")]
        desktop = FakeNode("desktop", children=[FakeNode("Example app", pid=88, children=frames)])
        adapter = AccessibilityBackend(FakeAtspi(desktop))
        with self.assertRaisesRegex(Exception, "несколько доступных окон"):
            adapter.inspect(88)
        tree = adapter.inspect(88, window_title="Tab B", window_key="0xbeef")
        self.assertEqual(tree["window_address"], "0xbeef")
        node = next(n for n in tree["nodes"] if n["name"] == "Save B")
        self.assertNotIn("Save A", [n["name"] for n in tree["nodes"]])
        self.assertFalse(adapter.act(88, tree["snapshot_id"], node["node_id"], "click", window_key="0xother")["ok"])
        self.assertTrue(adapter.act(88, tree["snapshot_id"], node["node_id"], "click", window_key="0xbeef")["ok"])
        self.assertEqual(one.pressed, 0)
        self.assertEqual(two.pressed, 1)

    def test_cdp_exact_tab_read_and_user_js_cannot_be_injected(self):
        class Socket:
            def __init__(self): self.sent = None; self.closed = False
            def settimeout(self, sec): pass
            def send(self, text): self.sent = json.loads(text)
            def recv(self): return json.dumps({"id": 1, "result": {"result": {"value": "Latest post body"}}})
            def close(self): self.closed = True
        sock = Socket()
        targets = [{"type": "page", "id": "tab-1", "title": "News", "url": "https://example.com/",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/tab-1"}]
        adapter = BrowserCdp(fetch=lambda path: targets, connect=lambda url: sock)
        self.assertEqual(adapter.list_tabs()["tabs"][0]["tab_id"], "tab-1")
        with self.assertRaises(BrowserCdpError):
            adapter.read_tab("not-listed")
        actual = adapter.read_tab("tab-1")
        self.assertEqual(actual["text"], "Latest post body")
        self.assertIn("document.body", sock.sent["params"]["expression"])
        self.assertFalse(sock.sent["params"]["userGesture"])
        self.assertTrue(sock.closed)

    def test_cdp_navigation_requires_exact_tab_and_external_url(self):
        class Socket:
            def __init__(self): self.messages = []; self.closed = False
            def settimeout(self, _): pass
            def send(self, raw): self.messages.append(json.loads(raw))
            def recv(self): return json.dumps({"id":1,"result":{"frameId":"abc"}})
            def close(self): self.closed = True
        socket = Socket()
        target = [{"type":"page","id":"tab-123","url":"https://example.org/",
                   "webSocketDebuggerUrl":"ws://127.0.0.1:9222/devtools/page/tab-123"}]
        adapter = BrowserCdp(fetch=lambda path: target, connect=lambda url: socket)
        with self.assertRaises(BrowserCdpError):
            adapter.navigate_tab("tab-123", "http://127.0.0.1:1234/admin")
        with self.assertRaises(BrowserCdpError):
            adapter.navigate_tab("tab-123", "file:///etc/passwd")
        with self.assertRaises(BrowserCdpError):
            adapter.navigate_tab("tab-123", "http://localhost/admin")
        with self.assertRaises(BrowserCdpError):
            adapter.navigate_tab("missing", "https://example.org")
        self.assertEqual(socket.messages, [])
        result = adapter.navigate_tab("tab-123", "https://example.org/news")
        self.assertTrue(result["navigation_started"])
        self.assertEqual(socket.messages[0]["method"], "Page.navigate")
        self.assertEqual(socket.messages[0]["params"]["url"], "https://example.org/news")
        self.assertTrue(socket.closed)

    def test_cdp_never_connects_to_remote_websocket(self):
        remote = [{"type":"page", "id":"id", "url":"https://example.org",
                   "webSocketDebuggerUrl":"ws://evil.example:9222/devtools/page/id"}]
        adapter = BrowserCdp(fetch=lambda path: remote, connect=lambda url: self.fail("remote WS accessed"))
        with self.assertRaises(BrowserCdpError):
            adapter.read_tab("id")

    def test_file_manager_uses_dbus_argv_not_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'hello "spaces"'
            path.mkdir()
            received = []
            def runner(argv, **kwargs):
                received.append((argv, kwargs))
                return type("Proc", (), {"returncode":0,"stdout":"()", "stderr":""})()
            result = reveal(str(path), run=runner)
            self.assertTrue(result["ok"])
            argv, kwargs = received[0]
            self.assertEqual(argv[0], "gdbus")
            self.assertTrue(argv[argv.index("--method")+1].endswith("ShowFolders"))
            self.assertIn(path.as_uri(), argv[-2])
            self.assertFalse(kwargs.get("shell", False))

    def test_editor_python_ast_and_literal_rg(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example.py"
            path.write_text("class Foo:\n    def run(self):\n        print('foo.bar')\n")
            outline = symbols(str(path))
            self.assertTrue(outline["ok"])
            self.assertEqual([x["name"] for x in outline["symbols"]], ["Foo", "run"])
            if __import__('shutil').which('rg'):
                matches = search('foo.bar', tmp)
                self.assertTrue(any("foo.bar" in m for m in matches['matches']))
                # --fixed-strings means '.' is not a regex wildcard.
                self.assertEqual(search('foo*bar', tmp)['matches'], [])

    def test_lsp_stdio_document_symbol_roundtrip(self):
        from purah_agent.editor import _lsp_outline
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake_lsp.py"
            server_code = (
                "import json, sys\n"
                "reader = sys.stdin.buffer\nwriter = sys.stdout.buffer\n"
                "while True:\n"
                "    headers = {}\n"
                "    while True:\n"
                "        line = reader.readline()\n"
                "        if not line: sys.exit(0)\n"
                "        if line == b'\\r\\n': break\n"
                "        key, val = line.split(b':', 1)\n"
                "        headers[key.lower().strip()] = val.strip()\n"
                "    message = json.loads(reader.read(int(headers[b'content-length'])))\n"
                "    if 'id' not in message: continue\n"
                "    if message['method'] == 'initialize':\n"
                "        result = {'capabilities': {'documentSymbolProvider': True}}\n"
                "    else:\n"
                "        result = [{'name':'hello', 'kind':12, 'range':{'start':{'line':2,'character':0}}}]\n"
                "    output = json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}).encode()\n"
                "    writer.write(('Content-Length: %d\\r\\n\\r\\n' % len(output)).encode()+output)\n"
                "    writer.flush()\n"
            )
            fake.write_text(server_code)
            file = Path(tmp) / "example.py"
            file.write_text("def hello(): pass\n")
            result = _lsp_outline(file, f"{sys.executable} {fake}")
            self.assertTrue(result['ok'])
            self.assertEqual(result['backend'], 'lsp')
            self.assertEqual(result['symbols'][0]['name'], 'hello')

    def test_shell_output_is_capped_in_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = TerminalManager(Path(tmp), state_dir=Path(tmp))
            with patch.dict(os.environ, {"PURAH_TERMINAL_MAX_OUTPUT_CHARS": "1024"}):
                result = manager.run_background("head -c 300000 /dev/zero | tr '\\000' a")
            self.assertTrue(result['ok'])
            self.assertTrue(result['output_truncated'])
            self.assertLess(len(result['output']), 1200)

    def test_tool_journal_persists_bounded_redacted_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            import sqlite3
            db_path = Path(tmp) / "memory.sqlite3"
            from contextlib import closing
            with closing(sqlite3.connect(db_path)) as db, db:
                tool_journal.initialize(db)
            tool_journal.append(db_path, "turn1", "run_terminal_command", {"command": "ls"},
                                json.dumps({"ok":True,"output":"folder 123 exists", "returncode":0}))
            tool_journal.append(db_path, "turn1", "telegram_send_message",
                                {"candidate_id":"private", "text":"PRIVATE HELLO"},
                                json.dumps({"ok":True,"text":"PRIVATE HELLO"}))
            tool_journal.append(db_path, "turn1", "run_terminal_command", {"command": "ls"},
                                json.dumps({"ok":True,"output":"api_key=supersecret", "returncode":0}))
            recent = tool_journal.recent(db_path, limit=5)
            self.assertEqual(len(recent), 3)
            payload = json.dumps(recent)
            self.assertIn('folder 123 exists', payload)
            self.assertNotIn('PRIVATE HELLO', payload)
            self.assertNotIn('supersecret', payload)
            self.assertNotIn('candidate_id', payload)

    def test_detached_existing_tmux_session_does_not_hide_explicit_terminal_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = TerminalManager(Path(tmp), state_dir=Path(tmp))
            manager._session_exists = lambda _: True
            from subprocess import CompletedProcess
            manager._tmux = lambda argv: CompletedProcess(argv, 1, "", "")
            manager._hypr_active = lambda: {}
            started = []
            class Launcher:
                pid = 177
            manager._popen = lambda *a, **kw: (started.append((a, kw)) or Launcher())
            with patch('shutil.which', side_effect=lambda name: '/usr/bin/' + name):
                self.assertFalse(manager._start_visible_session('purah-agent', Path(tmp)))
            self.assertEqual(len(started), 1)
            self.assertIn('new-session', started[0][0][0])

    def test_focus_restore_does_not_steal_focus_after_user_switches(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = TerminalManager(Path(tmp), state_dir=Path(tmp))
            manager._hypr_active = lambda: {"address":"0x99", "class":"firefox", "pid":9}
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                return type("P",(),{"returncode":0,"stdout":""})()
            manager._run = run
            restored = manager._restore_launch_focus({"address":"0x11"}, 123, "/usr/bin/kitty")
            self.assertFalse(restored)
            self.assertFalse(any('dispatch' in cmd for cmd in calls))
            manager._hypr_active = lambda: {"address":"0x22", "class":"kitty", "pid":123}
            restored = manager._restore_launch_focus({"address":"0x11"}, 123, "/usr/bin/kitty")
            self.assertTrue(restored)
            self.assertIn('address:0x11', calls[-1])


if __name__ == "__main__":
    unittest.main()
