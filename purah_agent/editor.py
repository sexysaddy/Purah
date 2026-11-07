"""Read-only code-editor adapter: LSP documentSymbol, Python AST, and rg/ctags CLI.

LSP servers are launched only from explicit user-owned config, never from a model
supplied command. No editor window or keyboard focus is required.
"""
from __future__ import annotations
import ast
import json
import os
import queue
import shlex
import shutil
import subprocess
import threading
from pathlib import Path


class EditorError(RuntimeError):
    pass


class _LspSession:
    def __init__(self, command: str, root: Path, *, timeout: float = 7):
        args = shlex.split(command)
        if not args or not shutil.which(args[0]):
            raise EditorError(f"LSP executable не найден: {args[0] if args else '<empty>'}")
        self.timeout = timeout
        env = os.environ.copy()
        for key in list(env):
            if any(marker in key.upper() for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_HASH")):
                env.pop(key, None)
        env.pop("SUDO_ASKPASS", None)
        self.proc = subprocess.Popen(args, cwd=str(root), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     start_new_session=True, env=env)
        self.q: queue.Queue = queue.Queue(maxsize=100)
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        self.counter = 0

    def _read_loop(self):
        try:
            reader = self.proc.stdout
            while reader is not None:
                headers = {}
                while True:
                    line = reader.readline()
                    if not line:
                        return
                    if line in {b"\r\n", b"\n"}:
                        break
                    if b":" in line:
                        name, val = line.split(b":", 1)
                        headers[name.lower().strip()] = val.strip()
                length = int(headers.get(b"content-length", b"0"))
                if length <= 0 or length > 2_000_000:
                    return
                payload = reader.read(length)
                if len(payload) != length:
                    return
                try:
                    obj = json.loads(payload)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                try:
                    self.q.put(obj, timeout=0.5)
                except queue.Full:
                    return
        except (ValueError, OSError):
            return

    def send(self, method: str, params, *, request: bool = True):
        self.counter += 1
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if request:
            message["id"] = self.counter
        data = json.dumps(message, ensure_ascii=False).encode("utf-8")
        assert self.proc.stdin is not None
        self.proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        self.proc.stdin.flush()
        return self.counter if request else None

    def request(self, method: str, params) -> object:
        import time
        request_id = self.send(method, params)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                item = self.q.get(timeout=max(0.01, min(0.25, deadline-time.monotonic())))
            except queue.Empty:
                if self.proc.poll() is not None:
                    break
                continue
            if isinstance(item, dict) and item.get("id") == request_id:
                if "error" in item:
                    raise EditorError("LSP error: " + str(item["error"])[:300])
                return item.get("result")
        raise EditorError(f"LSP timeout: {method}")

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                self.proc.wait(timeout=0.7)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait(timeout=1)
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            if self.proc.stdout:
                self.proc.stdout.close()
        except OSError:
            pass


def _lsp_outline(path: Path, cmd: str) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > 500_000:
        raise EditorError("Файл больше 500 KiB; LSP inspection ограничен для защиты памяти.")
    language = {".py": "python", ".rs": "rust", ".js": "javascript", ".ts": "typescript",
                ".tsx": "typescriptreact", ".go": "go", ".c": "c", ".cpp": "cpp"}.get(path.suffix.lower(), path.suffix.lstrip("."))
    sess = _LspSession(cmd, path.parent)
    try:
        sess.request("initialize", {"processId": os.getpid(), "rootUri": path.parent.as_uri(),
            "capabilities": {"textDocument": {"documentSymbol": {"hierarchicalDocumentSymbolSupport": True}}},
            "clientInfo": {"name": "PURAH", "version": "1.4.0"}})
        sess.send("initialized", {}, request=False)
        uri = path.as_uri()
        sess.send("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": language, "version": 1, "text": text,
        }}, request=False)
        result = sess.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
        symbols = []
        def visit(items, parent="", depth=0):
            if not isinstance(items, list) or depth > 8:
                return
            for item in items:
                if len(symbols) >= 200 or not isinstance(item, dict):
                    break
                where = item.get("range") or item.get("location", {}).get("range", {})
                start = where.get("start", {}) if isinstance(where, dict) else {}
                name = str(item.get("name", ""))[:160]
                symbols.append({"name": name, "parent": parent, "kind": item.get("kind"),
                                "line": int(start.get("line", 0)) + 1})
                visit(item.get("children"), name, depth + 1)
        visit(result)
        return {"ok": True, "backend": "lsp", "path": str(path), "symbols": symbols,
                "truncated": len(symbols) >= 200}
    finally:
        sess.close()


def _python_ast_outline(path: Path) -> dict:
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source, filename=str(path))
    result = []
    def visit(body, parent="", depth=0):
        for node in body:
            if len(result) >= 200:
                return
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                result.append({"name": node.name, "parent": parent,
                               "kind": type(node).__name__, "line": node.lineno})
                if depth < 8:
                    visit(node.body, node.name, depth + 1)
    visit(tree.body)
    return {"ok": True, "backend": "python-ast", "path": str(path),
            "symbols": result, "truncated": len(result) >= 200}


def symbols(path: str, *, lsp_command: str = "") -> dict:
    target = Path(path).expanduser().resolve(strict=True)
    if not target.is_file() or target.stat().st_size > 500_000:
        raise EditorError("Нужен существующий файл размером до 500 KiB.")
    if lsp_command:
        return _lsp_outline(target, lsp_command)
    if target.suffix.lower() == ".py":
        return _python_ast_outline(target)
    if shutil.which("ctags"):
        try:
            result = subprocess.run(["ctags", "--output-format=json", "--fields=+n", "-f", "-", str(target)],
                                    capture_output=True, text=True, timeout=5)
            entries = []
            for line in result.stdout.splitlines()[:200]:
                item = json.loads(line)
                if item.get("_type") == "tag":
                    entries.append({"name": item.get("name"), "kind": item.get("kind"), "line": item.get("line")})
            return {"ok": True, "backend": "ctags", "path": str(target), "symbols": entries}
        except (ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
            pass
    raise EditorError("Нет LSP/ctags для этого языка. Настрой editor.lsp_<language>_command или установи Universal Ctags.")


def search(pattern: str, directory: str, *, max_results: int = 40) -> dict:
    root = Path(directory).expanduser().resolve(strict=True)
    if not root.is_dir() or len(pattern) < 1 or len(pattern) > 200:
        raise EditorError("Нужен существующий каталог и строка поиска длиной 1–200.")
    if not shutil.which("rg"):
        raise EditorError("ripgrep (rg) не установлен.")
    # Stream and stop once we have enough matches. capture_output=True could
    # retain gigabytes when a pattern matches nearly every line in a tree.
    import select
    import time
    max_results = max(1, min(100, int(max_results)))
    proc = subprocess.Popen(["rg", "--no-heading", "--line-number", "--fixed-strings", "--max-count", "12",
                             "--glob", "!*.lock", "--", pattern, str(root)],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    buffer = b""
    deadline = time.monotonic() + 8
    truncated = False
    try:
        assert proc.stdout is not None
        while time.monotonic() < deadline:
            readable, _, _ = select.select([proc.stdout], [], [], 0.15)
            if readable:
                chunk = os.read(proc.stdout.fileno(), 8192)
                if not chunk:
                    break
                buffer += chunk
                if buffer.count(b"\n") > max_results or len(buffer) > 60_000:
                    truncated = True
                    break
            elif proc.poll() is not None:
                break
        else:
            raise EditorError("Тайм-аут поиска rg.")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
        if proc.stdout:
            proc.stdout.close()
    lines = buffer.decode("utf-8", errors="replace").splitlines()
    return {"ok": True, "backend": "ripgrep", "matches": [line[:500] for line in lines[:max_results]],
            "truncated": truncated or len(lines) > max_results}
