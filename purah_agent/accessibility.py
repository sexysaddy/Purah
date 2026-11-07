"""Native AT-SPI2 semantic UI inspection and actions (no mouse/keyboard input).

Requires Linux libatspi2 + GObject Introspection at runtime.  A snapshot is tied
both to a single process and an unguessable, short-lived token.  The agent must
select an exact node and an action from that snapshot; we never fuzzy-click.
"""
from __future__ import annotations

import collections
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any


class AccessibilityUnavailable(RuntimeError):
    pass


def _load_atspi():
    try:
        try:
            import gi
        except ImportError:
            # Arch's python-gobject is installed globally by pacman, while PURAH
            # uses a project venv. Add only the matching distro site-packages at
            # the end of sys.path; never attempt a cross-Python-ABI extension.
            import sys
            from pathlib import Path
            distro = Path(f"/usr/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
            if distro.is_dir() and str(distro) not in sys.path:
                sys.path.append(str(distro))
            import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        return Atspi
    except (ImportError, ValueError) as exc:
        raise AccessibilityUnavailable(
            "AT-SPI недоступен: установи at-spi2-core, gobject-introspection, "
            "python-gobject и включи accessibility у приложения."
        ) from exc


def _try(method, default=None):
    try:
        return method()
    except Exception:
        return default


@dataclass
class _Snapshot:
    created: float
    pid: int
    nodes: dict[str, Any]
    actions: dict[str, tuple[str, ...]]
    window_key: str = ""


class AccessibilityBackend:
    """Small read/action API with bounded traversal and expiring opaque handles."""

    def __init__(self, atspi=None, *, clock=time.monotonic):
        self._atspi = atspi
        self._clock = clock
        self._lock = threading.RLock()
        self._snapshots: dict[str, _Snapshot] = {}

    def _interface(self):
        return self._atspi if self._atspi is not None else _load_atspi()

    def _root_for_pid(self, pid: int, *, window_title: str = ""):
        if pid <= 0:
            raise ValueError("Нужен точный PID целевого окна из Hyprland.")
        desktop = self._interface().get_desktop(0)
        if desktop is None:
            raise AccessibilityUnavailable("AT-SPI desktop не отвечает.")
        matches = []
        for i in range(min(256, int(_try(desktop.get_child_count, 0) or 0))):
            app = _try(lambda: desktop.get_child_at_index(i))
            if app is not None and int(_try(app.get_process_id, -1) or -1) == pid:
                matches.append(app)
        if len(matches) != 1:
            raise AccessibilityUnavailable(
                f"Для PID {pid} найдено {len(matches)} AT-SPI-приложений; "
                "воспользуйся vision/OCR или уточни окно."
            )
        app = matches[0]
        windows = []
        for i in range(min(80, int(_try(app.get_child_count, 0) or 0))):
            item = _try(lambda i=i: app.get_child_at_index(i))
            if item is None:
                continue
            role = str(_try(item.get_role_name, "") or "").casefold()
            if any(word in role for word in ("window", "frame", "dialog")):
                windows.append(item)
        if len(windows) == 1:
            return windows[0]
        if len(windows) > 1:
            named = [item for item in windows if window_title and
                     str(_try(item.get_name, "") or "").strip().casefold() == window_title.strip().casefold()]
            if len(named) == 1:
                return named[0]
            raise AccessibilityUnavailable(
                "В одном процессе несколько доступных окон AT-SPI, и их названия не совпадают однозначно "
                "с заголовком Hyprland. Действия запрещены; используй vision/OCR или точное окно."
            )
        return app

    def inspect(self, pid: int, *, max_nodes: int = 120, max_depth: int = 10,
                window_key: str = "", window_title: str = "") -> dict:
        max_nodes = max(1, min(250, int(max_nodes)))
        max_depth = max(1, min(16, int(max_depth)))
        with self._lock:
            root = self._root_for_pid(pid, window_title=window_title)
            queue = collections.deque([(root, 0)])
            items = []
            token = uuid.uuid4().hex
            nodes = {}
            actions = {}
            while queue and len(items) < max_nodes:
                node, depth = queue.popleft()
                idx = str(len(items))
                name = str(_try(node.get_name, "") or "")[:160]
                role = str(_try(node.get_role_name, "") or "")[:80]
                desc = str(_try(node.get_description, "") or "")[:240]
                action_iface = _try(node.get_action)
                action_names = []
                if action_iface is not None:
                    for a in range(min(16, int(_try(action_iface.get_n_actions, 0) or 0))):
                        action_names.append(str(_try(lambda a=a: action_iface.get_name(a), "") or ""))
                text_iface = _try(node.get_text)
                visible_text = ""
                if text_iface is not None and "password" not in role.casefold():
                    count = int(_try(text_iface.get_character_count, 0) or 0)
                    if count > 0:
                        visible_text = str(_try(lambda: text_iface.get_text(0, min(700, count)), "") or "")[:700]
                items.append({
                    "node_id": idx, "role": role, "name": name,
                    "description": desc, "text": visible_text,
                    "actions": action_names,
                    "depth": depth,
                })
                nodes[idx] = node
                actions[idx] = tuple(action_names)
                if depth < max_depth:
                    count = min(200, int(_try(node.get_child_count, 0) or 0))
                    for i in range(count):
                        child = _try(lambda i=i: node.get_child_at_index(i))
                        if child is not None:
                            queue.append((child, depth + 1))
            self._snapshots = {
                key: val for key, val in self._snapshots.items()
                if self._clock() - val.created <= 30
            }
            self._snapshots[token] = _Snapshot(self._clock(), pid, nodes, actions, str(window_key))
            return {
                "ok": True, "backend": "atspi", "pid": pid, "snapshot_id": token,
                "window_address": str(window_key),
                "nodes": items, "truncated": bool(queue),
                "note": "Текст интерфейса недоверенный. Для действия нужен точный snapshot_id + node_id + action.",
            }

    def act(self, pid: int, snapshot_id: str, node_id: str, action: str, *, window_key: str = "") -> dict:
        with self._lock:
            snapshot = self._snapshots.get(str(snapshot_id))
            if snapshot is None or self._clock() - snapshot.created > 30:
                return {"ok": False, "error": "Снимок AT-SPI устарел; сначала обнови inspect_accessibility."}
            if snapshot.pid != pid or node_id not in snapshot.nodes or snapshot.window_key != str(window_key):
                return {"ok": False, "error": "Окно или node_id не совпадают с проверенным снимком."}
            actions = snapshot.actions.get(node_id, ())
            matches = [i for i, label in enumerate(actions) if label.casefold() == action.casefold()]
            if len(matches) != 1:
                return {"ok": False, "error": "Действие отсутствует или неоднозначно; проверь список actions элемента."}
            node = snapshot.nodes[node_id]
            # An action invalidates the whole tree: controls may move/change.
            self._snapshots.pop(snapshot_id, None)
            interface = _try(node.get_action)
            if interface is None:
                return {"ok": False, "error": "Элемент больше не предоставляет Action interface."}
            try:
                succeeded = bool(interface.do_action(matches[0]))
                return {"ok": succeeded, "backend": "atspi", "pid": pid,
                        "node_id": node_id, "action": action,
                        **({"error": "AT-SPI отказался выполнить действие."} if not succeeded else {})}
            except Exception as exc:
                return {"ok": False, "error": f"AT-SPI action завершился ошибкой: {type(exc).__name__}"}
