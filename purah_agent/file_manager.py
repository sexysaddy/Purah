"""File-manager adapter using the Freedesktop FileManager1 session D-Bus API."""
from __future__ import annotations
import subprocess
from pathlib import Path


class FileManagerError(RuntimeError):
    pass


def reveal(path: str, *, select: bool = False, run=subprocess.run) -> dict:
    """Explicit GUI operation; never used for a mere 'list files' request."""
    target = Path(path).expanduser().resolve(strict=False)
    if not target.exists():
        raise FileManagerError(f"Путь не существует: {target}")
    method = "ShowItems" if select or target.is_file() else "ShowFolders"
    # A file:// URI is percent-encoded. JSON double-quoted strings are accepted
    # by GVariant parsers (and never go through a shell).
    import json
    arg = "[" + json.dumps(target.as_uri(), ensure_ascii=True) + "]"
    command = ["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
               "--object-path", "/org/freedesktop/FileManager1",
               "--method", "org.freedesktop.FileManager1." + method, arg, ""]
    try:
        reply = run(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FileManagerError("D-Bus FileManager1 недоступен; нужен gdbus и поддерживающий его менеджер файлов.") from exc
    if reply.returncode:
        raise FileManagerError("FileManager1 не выполнил запрос: " + (reply.stderr or "")[:500])
    return {"ok": True, "backend": "dbus", "path": str(target), "method": method,
            "message": "Папка открыта в файловом менеджере." if method == "ShowFolders" else "Объект выделен в файловом менеджере."}
