"""Yandex Music playback через долгоживущий mpv-демон (MPRIS включён через mpv-mpris).

Модуль не печатает ничего сам: все функции возвращают dict, main.py упаковывает его
в JSON-результат инструмента. Токен берётся из ~/.config/purah/yandex_token
(или PURAH_YANDEX_TOKEN_FILE).
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

SOCKET_NAME = "purah-music.sock"
MIN_AHEAD = 3
KNOWN_LIMIT = 80

MY_WAVE = "user:onyourwave"


# ---------------------------------------------------------------- пути / окружение

def _runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(base)


def socket_path() -> Path:
    return _runtime_dir() / SOCKET_NAME


def state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    root = Path(base) / "purah"
    root.mkdir(parents=True, exist_ok=True)
    return root / "music_state.json"


def log_path() -> Path:
    return state_path().with_name("music-mpv.log")


def token_path() -> Path:
    env = os.environ.get("PURAH_YANDEX_TOKEN_FILE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "purah" / "yandex_token"


# ---------------------------------------------------------------- yandex client

_client = None
_stations_cache: list[dict] | None = None


def client():
    global _client
    if _client is not None:
        return _client
    from yandex_music import Client  # импорт здесь: без библиотеки модуль всё равно грузится

    path = token_path()
    if not path.is_file():
        raise RuntimeError(f"Нет файла с токеном Яндекс.Музыки: {path}")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(f"Файл токена пустой: {path}")
    _client = Client(token).init()
    return _client


def _stations() -> list[dict]:
    global _stations_cache
    if _stations_cache is None:
        items = []
        for res in client().rotor_stations_list() or []:
            st = res.station
            if not st or not st.id:
                continue
            sid = st.id
            if isinstance(sid, str):
                value = sid
            else:
                st_type = getattr(sid, "type", None)
                tag = getattr(sid, "tag", None)
                value = f"{st_type}:{tag}" if st_type and tag else ""
            if value:
                items.append({"value": value, "name": st.name or value})
        _stations_cache = items
    return _stations_cache


def find_station(query: str) -> str | None:
    q = (query or "").strip().casefold()
    if not q:
        return None
    items = _stations()
    for item in items:
        if item["name"].casefold() == q:
            return item["value"]
    for item in items:
        if q in item["name"].casefold():
            return item["value"]
    return None


# ---------------------------------------------------------------- mpv daemon

class _Conn:
    def __init__(self, path: Path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(str(path))
        self.buf = b""
        self.rid = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.sock.close()
        except OSError:
            pass

    def cmd(self, *args: Any, timeout: float = 15.0) -> Any:
        self.rid += 1
        payload = json.dumps({"command": list(args), "request_id": self.rid}) + "\n"
        self.sock.sendall(payload.encode())
        deadline = time.time() + timeout
        while time.time() < deadline:
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("request_id") != self.rid:
                    continue
                if msg.get("error") not in (None, "success"):
                    raise RuntimeError(f"mpv: {msg.get('error')} ({args[0]})")
                return msg.get("data")
            remaining = max(0.05, deadline - time.time())
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("mpv закрыл IPC-сокет")
            self.buf += chunk
        raise TimeoutError(f"mpv не ответил на {args[0]}")


def _get(conn: "_Conn", prop: str, default=None):
    """Необязательное свойство: mpv может временно ответить "property unavailable"."""
    try:
        value = conn.cmd("get_property", prop)
    except (RuntimeError, TimeoutError):
        return default
    return default if value is None else value


def _alive(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with _Conn(path):
            return True
    except OSError:
        return False


def ensure_daemon() -> Path:
    path = socket_path()
    if _alive(path):
        return path
    mpv = shutil.which("mpv")
    if not mpv:
        raise RuntimeError("mpv не установлен: sudo pacman -S mpv mpv-mpris")
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    log = log_path()
    with log.open("ab") as handle:
        subprocess.Popen(
            [mpv, "--no-video", "--idle=yes", f"--input-ipc-server={path}",
             "--vo=null", "--audio-display=no", "--force-window=no",
             "--no-terminal", "--really-quiet", "--msg-level=all=warn",
             "--title=PURAH Music"],
            stdin=subprocess.DEVNULL, stdout=handle, stderr=handle,
            start_new_session=True, close_fds=True,
        )
    deadline = time.time() + 6
    while time.time() < deadline:
        if _alive(path):
            return path
        time.sleep(0.15)
    raise RuntimeError(f"mpv не поднялся, смотри {log}")


def _conn() -> _Conn:
    return _Conn(ensure_daemon())


# ---------------------------------------------------------------- состояние

def load_state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("tracks", [])
            data.setdefault("seen", [])
            return data
    except (OSError, ValueError):
        pass
    return {"mode": None, "station": None, "label": "", "tracks": [], "seen": [], "pending": []}


def save_state(state: dict) -> None:
    if len(state.get("seen") or []) > KNOWN_LIMIT:
        state["seen"] = state["seen"][-KNOWN_LIMIT:]
    state_path().write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _entry(track) -> dict:
    artists = ", ".join(a.name for a in (track.artists or []) if getattr(a, "name", None))
    return {
        "id": str(track.id),
        "title": track.title or "?",
        "artists": artists,
        "label": f"{artists} — {track.title}" if artists else (track.title or "?"),
    }


def _url_for(track_id: str) -> str:
    found = client().tracks([track_id])
    if not found:
        raise RuntimeError(f"трек {track_id} не найден")
    infos = found[0].get_download_info(get_direct_links=True) or []
    infos = [i for i in infos if i.direct_link]
    if not infos:
        raise RuntimeError(f"нет прямой ссылки на трек {track_id} (нужна подписка)")
    best = max(infos, key=lambda i: i.bitrate_in_kbps or 0)
    return best.direct_link


def _append(conn: _Conn, entry: dict) -> None:
    conn.cmd("loadfile", _url_for(entry["id"]), "append-play", -1,
             {"force-media-title": entry["label"]})


def _remember(state: dict, entry: dict) -> bool:
    if entry["id"] in state["seen"]:
        return False
    state["tracks"].append(entry)
    state["seen"].append(entry["id"])
    return True


def _fill(state: dict, conn: _Conn, want: int = MIN_AHEAD) -> int:
    """Догружает очередь, чтобы впереди было хотя бы want треков. Возвращает число добавленных."""
    added = 0
    mode = state.get("mode")
    if mode in {"station", "track"}:
        while _ahead(conn, state) < want:
            last = state["tracks"][-1]["id"] if state["tracks"] else None
            result = client().rotor_station_tracks(state["station"], queue=last) if last \
                else client().rotor_station_tracks(state["station"])
            fresh = 0
            for item in (result.sequence if result else []) or []:
                if not getattr(item, "track", None):
                    continue
                entry = _entry(item.track)
                if _remember(state, entry):
                    _append(conn, entry)
                    fresh += 1
            added += fresh
            if not fresh:
                break
    elif mode == "playlist":
        while _ahead(conn, state) < want and state.get("pending"):
            entry = state["pending"].pop(0)
            if _remember(state, entry):
                _append(conn, entry)
                added += 1
    return added


def _settle(conn: "_Conn", timeout: float = 2.0) -> None:
    """Ждёт, пока mpv реально загрузит файл (иначе time-pos/duration ещё недоступны)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _get(conn, "time-pos") is not None:
            return
        if _get(conn, "playlist-pos") is None:
            return
        time.sleep(0.1)


def _unpause(conn: "_Conn") -> None:
    """Старт нового источника всегда должен звучать, даже если до этого стояла пауза."""
    try:
        conn.cmd("set_property", "pause", False)
    except (RuntimeError, TimeoutError):
        pass


def _reset(conn: _Conn) -> None:
    conn.cmd("stop")
    conn.cmd("playlist-clear")


def _ahead(conn: _Conn, state: dict) -> int:
    pos = _get(conn, "playlist-pos")
    if pos is None:
        return 0
    return max(0, len(state["tracks"]) - (int(pos) + 1))


# ---------------------------------------------------------------- запуск источников

def _start_station(station: str, label: str) -> dict:
    state = {"mode": "station", "station": station, "label": label,
             "tracks": [], "seen": [], "pending": []}
    with _conn() as conn:
        _reset(conn)
        _fill(state, conn, want=MIN_AHEAD)
        _settle(conn)
        _unpause(conn)
    if not state["tracks"]:
        save_state({"mode": None, "station": None, "label": "", "tracks": [], "seen": [], "pending": []})
        raise RuntimeError(f"станция «{label}» не отдала треки")
    save_state(state)
    return {"started": state["tracks"][0]["label"], "source": label, "mode": "station"}


def _start_track(track) -> dict:
    entry = _entry(track)
    state = {"mode": "track", "station": f"track:{entry['id']}", "label": "радио по треку",
             "tracks": [], "seen": [], "pending": []}
    with _conn() as conn:
        _reset(conn)
        if _remember(state, entry):
            _append(conn, entry)
        _fill(state, conn, want=MIN_AHEAD)
        _settle(conn)
        _unpause(conn)
    save_state(state)
    return {"started": entry["label"], "source": "поиск", "mode": "track"}


def _liked_entries(limit: int = 60) -> list[dict]:
    """Свежие лайки: в users_likes_tracks() новые треки идут первыми."""
    likes = client().users_likes_tracks()
    ids = [str(getattr(item, "id", None) or "").split(":")[0] for item in (likes.tracks or [])]
    ids = [i for i in ids if i]
    if not ids:
        raise RuntimeError("в «Мне нравится» пусто")
    ids = ids[:max(1, limit)]
    found = {str(t.id): t for t in (client().tracks(ids) or [])}
    return [_entry(found[i]) for i in ids if i in found]


def _start_liked(limit: int = 60) -> dict:
    entries = _liked_entries(limit)
    if not entries:
        raise RuntimeError("лайки не отдали треки")
    label = f"Мне нравится ({len(entries)})"
    state = {"mode": "playlist", "station": None, "label": label,
             "tracks": [], "seen": [], "pending": entries[1:]}
    with _conn() as conn:
        _reset(conn)
        first = entries[0]
        _remember(state, first)
        _append(conn, first)
        _fill(state, conn, want=MIN_AHEAD)
        _settle(conn)
        _unpause(conn)
    save_state(state)
    return {"started": first["label"], "source": "лайки", "mode": "playlist"}


def _start_playlist(name: str) -> dict:
    q = name.strip().casefold()
    playlists = client().users_playlists_list() or []
    chosen = None
    for pl in playlists:
        if (pl.title or "").strip().casefold() == q:
            chosen = pl
            break
    if chosen is None:
        for pl in playlists:
            if q and q in (pl.title or "").strip().casefold():
                chosen = pl
                break
    if chosen is None:
        titles = ", ".join((pl.title or "?") for pl in playlists) or "нет плейлистов"
        raise RuntimeError(f"плейлист «{name}» не найден. Есть: {titles}")
    full = chosen
    if not (chosen.tracks or []):
        owner = getattr(getattr(chosen, "owner", None), "uid", None)
        full = client().users_playlists(chosen.kind, owner) or chosen
    ids = [str(st.track.id) for st in (full.tracks or []) if getattr(st, "track", None)]
    if not ids:
        raise RuntimeError(f"плейлист «{chosen.title}» пустой")
    found = {str(t.id): t for t in (client().tracks(ids) or [])}
    pending = [_entry(found[i]) for i in ids if i in found]
    state = {"mode": "playlist", "station": None, "label": chosen.title or name,
             "tracks": [], "seen": [], "pending": pending[1:]}
    with _conn() as conn:
        _reset(conn)
        first = pending[0]
        _remember(state, first)
        _append(conn, first)
        _fill(state, conn, want=MIN_AHEAD)
        _settle(conn)
        _unpause(conn)
    save_state(state)
    return {"started": pending[0]["label"], "source": f"плейлист {chosen.title}", "mode": "playlist"}


# ---------------------------------------------------------------- публичное API

def _norm(value: str) -> str:
    return "".join(ch for ch in value.casefold().replace("ё", "е") if ch.isalnum())


def play(query: str) -> dict:
    text = (query or "").strip()
    low = text.casefold().replace("ё", "е")
    if not text or low in {"моя волна", "мою волну", "мояволна", "волна", "my wave"}:
        return _start_station(MY_WAVE, "Моя волна")
    for prefix in ("включи ", "врубай ", "поставь ", "запусти "):
        if low.startswith(prefix):
            text = text[len(prefix):].strip()
            low = text.casefold().replace("ё", "е")
            break
    if not text or low in {"моя волна", "мою волну", "волна"}:
        return _start_station(MY_WAVE, "Моя волна")
    if low.startswith(("моя волна", "мою волну")):
        return _start_station(MY_WAVE, "Моя волна")
    for prefix in ("волна ", "волну ", "радио ", "станция "):
        if low.startswith(prefix):
            name = text[len(prefix):].strip()
            station = find_station(name)
            if not station:
                raise RuntimeError(f"станция «{name}» не найдена")
            return _start_station(station, name)
    if low in {"мне нравится", "мне нравилось", "лайки", "лайкнутые", "любимые",
               "мои лайки", "последние лайки", "последние понравившиеся",
               "последнее понравившееся", "последние лайкнутые", "что я лайкал"}:
        return _start_liked()
    for prefix in ("плейлист ", "плейлиста "):
        if low.startswith(prefix):
            return _start_playlist(text[len(prefix):].strip())
    found = client().search(text, type_="all")
    results = getattr(getattr(found, "tracks", None), "results", None) or []
    artist_hits = getattr(getattr(found, "artists", None), "results", None) or []
    if " - " not in text and " – " not in text:
        for artist in artist_hits:
            if _norm(artist.name or "") == _norm(text):
                return _start_station(f"artist:{artist.id}", artist.name or text)
    if results:
        return _start_track(results[0])
    if artist_hits:
        artist = artist_hits[0]
        return _start_station(f"artist:{artist.id}", artist.name or text)
    station = find_station(text)
    if station:
        return _start_station(station, text)
    raise RuntimeError(f"ничего не нашла по запросу «{text}»")


def control(action: str, value: str = "") -> dict:
    act = (action or "").strip().casefold()
    state = load_state()
    with _conn() as conn:
        if act == "pause":
            conn.cmd("set_property", "pause", True)
        elif act in {"resume", "play"}:
            if _get(conn, "playlist-pos") is None and state.get("tracks"):
                last = state["tracks"][-1]
                _append(conn, last)
                _settle(conn)
            conn.cmd("set_property", "pause", False)
        elif act == "toggle":
            conn.cmd("cycle", "pause")
        elif act == "next":
            conn.cmd("playlist-next", "force")
            _settle(conn)
            _fill(state, conn)
        elif act in {"previous", "prev"}:
            conn.cmd("playlist-prev", "force")
            _settle(conn)
        elif act == "stop":
            conn.cmd("stop")
            _settle(conn, timeout=1.0)
            state = {"mode": None, "station": None, "label": "", "tracks": [], "seen": [], "pending": []}
        elif act == "volume":
            try:
                vol = float(str(value).replace(",", "."))
            except ValueError:
                raise RuntimeError("для volume нужно число 0..100")
            if not 0 <= vol <= 100:
                raise RuntimeError("громкость должна быть 0..100")
            conn.cmd("set_property", "volume", vol)
        elif act == "seek":
            try:
                seconds = float(str(value).replace(",", "."))
            except ValueError:
                raise RuntimeError("для seek нужно число секунд (можно отрицательное)")
            conn.cmd("seek", seconds, "absolute" if seconds >= 0 else "relative")
        elif act == "status":
            if _get(conn, "playlist-pos") is not None:
                _fill(state, conn)
        else:
            raise RuntimeError(f"неизвестное действие: {action}")
        save_state(state)
        return _status(conn, state)


def _status(conn: _Conn, state: dict) -> dict:
    idle = bool(_get(conn, "idle-active", False))
    pos = _get(conn, "playlist-pos")
    title = _get(conn, "media-title") if pos is not None and not idle else None
    paused = bool(_get(conn, "pause", False)) if title else False
    position = _get(conn, "time-pos") if pos is not None else None
    duration = _get(conn, "duration") if pos is not None else None
    volume = _get(conn, "volume")
    upcoming = []
    if pos is not None and not idle:
        upcoming = [t["label"] for t in state["tracks"][int(pos) + 1:int(pos) + 4]]
    if idle or (pos is None and not title):
        state_label = "stopped"
    elif not title:
        state_label = "loading"
    else:
        state_label = "paused" if paused else "playing"
    return {
        "playing": title,
        "state": state_label,
        "source": state.get("label") or "",
        "position": round(position, 1) if isinstance(position, (int, float)) else None,
        "duration": round(duration, 1) if isinstance(duration, (int, float)) else None,
        "volume": round(volume, 0) if isinstance(volume, (int, float)) else None,
        "next": upcoming,
    }


def status() -> dict:
    state = load_state()
    with _conn() as conn:
        if _get(conn, "playlist-pos") is not None:
            _fill(state, conn)
            save_state(state)
        return _status(conn, state)


# ---------------------------------------------------------------- CLI для проверки

def _cli(argv: list[str]) -> int:
    if not argv:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    cmd = argv[0].casefold()
    try:
        if cmd == "play":
            print(json.dumps(play(" ".join(argv[1:])), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(control(cmd, " ".join(argv[1:])), ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001 - CLI-обёртка
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_cli(sys.argv[1:]))