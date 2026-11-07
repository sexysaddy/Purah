"""Safe, background Telegram operations for PURAH.

This module uses a local Telegram *user* session through Telethon when the
optional dependency is installed.  It never drives Telegram Desktop, never
uses the mouse or keyboard, and never sends a recipient selected by a fuzzy
name match without an explicit confirmation.

The public methods are synchronous on purpose: PURAH's model worker already
runs tools off the Qt GUI thread.  A real Telethon client is kept on a private
asyncio thread; tests can inject a small fake client without installing
Telethon.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_RESULT_CHARS = 16_000
DEFAULT_RESOLUTION_TTL = 300.0
DEFAULT_SESSION_PATH = Path.home() / ".config" / "purah" / "telegram.session"


class TelegramUnavailable(RuntimeError):
    """The optional connector is not configured or Telethon is unavailable."""


def _normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    return " ".join(re.findall(r"[a-z0-9а-я]+", text))


def _tokens(value: Any) -> list[str]:
    return _normalise(value).split()


def _forms(token: str) -> set[str]:
    """Return conservative Russian case variants for matching only.

    These forms are a ranking aid, not permission to send.  A unique fuzzy
    result still requires confirmation, which prevents ``Ивану`` from being
    silently treated as a different ``Иван`` when contacts are ambiguous.
    """
    token = _normalise(token)
    if len(token) < 4:
        return {token}
    forms = {token}
    for suffix in ("ами", "ями", "ого", "ему", "ому", "ыми", "ими", "а", "я", "у", "ю", "е", "ы", "и", "ом", "ой"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            forms.add(token[: -len(suffix)])
            break
    return forms


@dataclass(frozen=True)
class TelegramCandidate:
    token: str
    peer_id: str
    kind: str
    title: str
    username: str = ""
    first_name: str = ""
    last_name: str = ""
    match: str = "exact"
    score: int = 0

    def public(self) -> dict[str, Any]:
        label = self.title.strip() or self.username.strip() or self.peer_id
        if self.username:
            label = f"{label} (@{self.username.lstrip('@')})"
        return {
            "candidate_id": self.token,
            "label": label,
            "kind": self.kind,
            "match": self.match,
        }


@dataclass(frozen=True)
class Resolution:
    query: str
    status: str
    candidates: tuple[TelegramCandidate, ...]
    resolution_id: str
    requires_confirmation: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "ok": True,
            "status": self.status,
            "query": self.query,
            "resolution_id": self.resolution_id,
            "requires_confirmation": self.requires_confirmation,
            "candidates": [candidate.public() for candidate in self.candidates],
            "instruction": (
                "Попроси пользователя выбрать один candidate_id или подтвердить fuzzy-совпадение; "
                "не отправляй сообщение до этого."
                if self.requires_confirmation or len(self.candidates) != 1 else ""
            ),
        }


@dataclass
class _BoundCandidate:
    candidate: TelegramCandidate
    entity: Any
    expires_at: float
    requires_confirmation: bool
    attempted: bool = False


def _entity_kind(entity: Any, dialog: Any = None) -> str:
    if bool(getattr(entity, "broadcast", False)) or bool(getattr(entity, "megagroup", False)):
        return "channel" if bool(getattr(entity, "broadcast", False)) else "group"
    name = type(entity).__name__.casefold()
    if "channel" in name:
        return "channel"
    if "chat" in name:
        return "group"
    return "user"


def _entity_display(entity: Any, dialog: Any = None) -> tuple[str, str, str, str]:
    title = str(getattr(dialog, "name", "") or getattr(entity, "title", "") or "").strip()
    first = str(getattr(entity, "first_name", "") or "").strip()
    last = str(getattr(entity, "last_name", "") or "").strip()
    username = str(getattr(entity, "username", "") or "").strip()
    if not title:
        title = " ".join(part for part in (first, last) if part).strip() or username
    return title, username, first, last


def _candidate_score(query: str, entity: Any, dialog: Any = None) -> tuple[int, str] | None:
    title, username, first, last = _entity_display(entity, dialog)
    query_norm = _normalise(query)
    if not query_norm:
        return None
    fields = [title, username, " ".join(part for part in (first, last) if part)]
    field_norm = [_normalise(field) for field in fields if _normalise(field)]
    if not field_norm:
        return None
    if query_norm in field_norm:
        return 100, "exact"
    query_tokens = _tokens(query)
    best = 0
    for field in field_norm:
        candidate_tokens = _tokens(field)
        score = 0
        for wanted in query_tokens:
            wanted_forms = _forms(wanted)
            token_scores = []
            for got in candidate_tokens:
                got_forms = _forms(got)
                if wanted == got:
                    token_scores.append(30)
                elif wanted_forms & got_forms:
                    token_scores.append(20)
                elif len(wanted) >= 4 and (got.startswith(wanted) or wanted.startswith(got)):
                    token_scores.append(12)
            if not token_scores:
                break
            score += max(token_scores)
        else:
            best = max(best, score)
    if best >= 24:
        return best, "fuzzy"
    return None


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _collect_dialogs(client: Any) -> list[tuple[Any, Any]]:
    result = client.iter_dialogs()
    if inspect.isawaitable(result):
        result = await result
    if hasattr(result, "__aiter__"):
        rows = []
        async for dialog in result:
            rows.append((getattr(dialog, "entity", dialog), dialog))
        return rows
    rows = []
    for dialog in result or []:
        rows.append((getattr(dialog, "entity", dialog), dialog))
    return rows


class _TelethonThread:
    """Own a single Telethon event loop and client thread."""

    def __init__(self, api_id: int, api_hash: str, session_path: Path):
        self.api_id = int(api_id)
        self.api_hash = api_hash
        self.session_path = session_path
        self.loop: asyncio.AbstractEventLoop | None = None
        self.client: Any = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="purah-telegram", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)
        if self._error:
            raise TelegramUnavailable(f"Не удалось запустить Telegram: {type(self._error).__name__}")
        if not self.loop or not self.client:
            raise TelegramUnavailable("Telegram-клиент не запустился")

    def _run(self) -> None:
        try:
            from telethon import TelegramClient  # optional dependency

            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.session_path.touch(exist_ok=True)
                os.chmod(self.session_path, 0o600)
            except OSError:
                pass
            self.client = TelegramClient(str(self.session_path), self.api_id, self.api_hash)
            self.loop.run_until_complete(self.client.connect())
            if not self.loop.run_until_complete(self.client.is_user_authorized()):
                self._error = TelegramUnavailable("Telegram не авторизован; запусти setup_telegram.py")
        except BaseException as exc:  # do not leak Telethon details to model
            self._error = exc
        finally:
            self._ready.set()
            if self.loop and self.client and not self._error:
                self.loop.run_forever()

    def call(self, coroutine: Any) -> Any:
        if not self.loop or self._error:
            raise TelegramUnavailable("Telegram-клиент недоступен")
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        return future.result(timeout=30)

    def close(self) -> None:
        if self.loop and self.client:
            try:
                self.call(self.client.disconnect())
            except Exception:
                pass
            self.loop.call_soon_threadsafe(self.loop.stop)


class TelegramConnector:
    """Recipient-safe background Telegram connector.

    ``client=`` is intended for tests and adapters.  It may expose synchronous
    or asynchronous Telethon-like methods.  Production instances use the
    private thread above and are created only after local setup is complete.
    """

    def __init__(
        self,
        api_id: int | str | None = None,
        api_hash: str = "",
        *,
        session_path: str | Path | None = None,
        client: Any = None,
        resolution_ttl: float = DEFAULT_RESOLUTION_TTL,
    ):
        self._thread: _TelethonThread | None = None
        self._client = client
        if client is None:
            if not api_id or not api_hash:
                raise TelegramUnavailable("Telegram отключён: не заданы api_id и api_hash")
            try:
                self._thread = _TelethonThread(int(api_id), str(api_hash), Path(session_path or DEFAULT_SESSION_PATH).expanduser())
                self._client = self._thread.client
            except (ValueError, TypeError) as exc:
                raise TelegramUnavailable("Telegram api_id должен быть числом") from exc
        self.resolution_ttl = max(30.0, float(resolution_ttl))
        self._bound: dict[str, _BoundCandidate] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_environment(cls) -> "TelegramConnector":
        api_id = os.getenv("PURAH_TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("PURAH_TELEGRAM_API_HASH", "").strip()
        session = os.getenv("PURAH_TELEGRAM_SESSION", "").strip() or str(DEFAULT_SESSION_PATH)
        if not api_id or not api_hash:
            raise TelegramUnavailable("Telegram отключён: задай PURAH_TELEGRAM_API_ID и PURAH_TELEGRAM_API_HASH")
        return cls(api_id, api_hash, session_path=session)

    async def _invoke_async(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if not self._client:
            raise TelegramUnavailable("Telegram-клиент недоступен")
        value = getattr(self._client, method)(*args, **kwargs)
        return await _maybe_await(value)

    def _invoke(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if self._thread:
            return self._thread.call(self._invoke_async(method, *args, **kwargs))
        return asyncio.run(self._invoke_async(method, *args, **kwargs))

    def resolve_recipient(self, query: str) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            return {"ok": False, "error": "recipient_query_required"}
        # Saved Messages is a stable Telegram special peer and needs no fuzzy lookup.
        if _normalise(query) in {"избранное", "сохраненные сообщения", "сохраненные", "saved messages", "me"}:
            candidate_token = secrets.token_urlsafe(18)
            candidate = TelegramCandidate(candidate_token, "me", "saved_messages", "Избранное", match="exact", score=100)
            with self._lock:
                self._bound[candidate_token] = _BoundCandidate(candidate, "me", time.monotonic() + self.resolution_ttl, False)
            return Resolution(query, "unique_exact", (candidate,), secrets.token_urlsafe(12)).public()

        rows = self._invoke("iter_dialogs")
        # _invoke cannot await an async generator, so collect through the helper
        # when the client exposes one (the production adapter uses this path).
        if hasattr(rows, "__aiter__"):
            if self._thread:
                rows = self._thread.call(_collect_dialogs(self._client))
            else:
                rows = asyncio.run(_collect_dialogs(self._client))
        elif rows and isinstance(rows, list) and rows and not isinstance(rows[0], tuple):
            rows = [(getattr(row, "entity", row), row) for row in rows]
        matches: list[tuple[int, str, Any, Any]] = []
        for entity, dialog in rows or []:
            scored = _candidate_score(query, entity, dialog)
            if scored:
                score, match = scored
                matches.append((score, match, entity, dialog))
        matches.sort(key=lambda row: (-row[0], _entity_display(row[2], row[3])[0].casefold()))
        if not matches:
            return {
                "ok": True,
                "status": "not_found",
                "query": query,
                "resolution_id": secrets.token_urlsafe(12),
                "candidates": [],
                "instruction": "Не отправляй сообщение; сообщи, что получатель не найден.",
            }

        candidates: list[TelegramCandidate] = []
        for score, match, entity, dialog in matches[:8]:
            title, username, first, last = _entity_display(entity, dialog)
            token = secrets.token_urlsafe(18)
            candidate = TelegramCandidate(token, str(getattr(entity, "id", title)), _entity_kind(entity, dialog), title, username, first, last, match, score)
            candidates.append(candidate)
            with self._lock:
                self._bound[token] = _BoundCandidate(candidate, entity, time.monotonic() + self.resolution_ttl, match != "exact")
        exact = [candidate for candidate in candidates if candidate.match == "exact"]
        if len(exact) == 1 and len(candidates) == 1:
            status, needs_confirmation = "unique_exact", False
        elif len(candidates) == 1:
            status, needs_confirmation = "confirmation_required", True
        else:
            status, needs_confirmation = "ambiguous", True
        return Resolution(query, status, tuple(candidates), secrets.token_urlsafe(12), needs_confirmation).public()

    def _take_bound(self, candidate_id: str, confirmed: bool) -> _BoundCandidate | dict[str, Any]:
        with self._lock:
            bound = self._bound.get(str(candidate_id or ""))
            if not bound or bound.expires_at <= time.monotonic():
                return {"ok": False, "error": "recipient_resolution_expired", "hint": "Сначала заново resolve_recipient."}
            if bound.attempted:
                return {"ok": False, "error": "send_already_attempted", "hint": "Не повторяй отправку автоматически: предыдущий запрос мог дойти до Telegram."}
            if bound.requires_confirmation and not confirmed:
                return {"ok": False, "error": "recipient_confirmation_required", "hint": "Попроси пользователя подтвердить именно этого кандидата."}
            bound.attempted = True
            return bound

    def send_message(self, candidate_id: str, text: str, *, confirmed: bool = False) -> dict[str, Any]:
        if not str(text or "").strip():
            return {"ok": False, "error": "message_text_required"}
        bound = self._take_bound(candidate_id, confirmed)
        if isinstance(bound, dict):
            return bound
        try:
            result = self._invoke("send_message", bound.entity, str(text))
            message_id = getattr(result, "id", None)
            return {"ok": True, "status": "sent", "candidate_id": candidate_id, "message_id": message_id}
        except TimeoutError:
            return {"ok": False, "status": "uncertain", "may_have_sent": True, "error": "telegram_timeout", "hint": "Не повторяй автоматически; проверь чат."}
        except Exception as exc:
            return {"ok": False, "status": "failed", "error": type(exc).__name__}

    def latest_message(self, candidate_id: str) -> dict[str, Any]:
        with self._lock:
            bound = self._bound.get(str(candidate_id or ""))
            if not bound or bound.expires_at <= time.monotonic():
                return {"ok": False, "error": "recipient_resolution_expired"}
        try:
            messages = self._invoke("get_messages", bound.entity, limit=1)
            if messages is None:
                message = None
            elif isinstance(messages, (list, tuple)):
                message = messages[0] if messages else None
            else:
                message = messages
            text = str(getattr(message, "message", "") or getattr(message, "text", "") or "").strip()
            return {"ok": True, "status": "found" if message else "empty", "candidate_id": candidate_id, "text": text[:MAX_RESULT_CHARS], "message_id": getattr(message, "id", None), "date": str(getattr(message, "date", "") or "")}
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}

    def close(self) -> None:
        if self._thread:
            self._thread.close()
            self._thread = None
        self._client = None


def telegram_status_from_environment() -> dict[str, Any]:
    """Return safe diagnostics; never echo api_hash or session paths."""
    api_id = os.getenv("PURAH_TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("PURAH_TELEGRAM_API_HASH", "").strip()
    try:
        import telethon  # type: ignore
        version = str(getattr(telethon, "__version__", "installed"))
    except Exception:
        version = "not-installed"
    return {"configured": bool(api_id and api_hash), "telethon": version, "api_id_present": bool(api_id), "api_hash_present": bool(api_hash)}

