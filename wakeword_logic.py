"""Strict, low-false-positive helpers for PURAH's offline wake phrase."""
from __future__ import annotations

import re


def normalize_wake_text(text: str) -> str:
    """Normalize punctuation and the common Russian ё/е variation."""
    return " ".join(re.findall(r"[a-zа-яё]+", str(text or "").casefold().replace("ё", "е")))


def name_variants(name: str) -> list[str]:
    """Common spoken variants of the wake name (diminutives etc.), normalized.

    ``пура`` -> ``пурочка``, ``пурушка``, ``пуронька`` ... so any affectionate
    form the user naturally says still wakes or interrupts PURAH.
    """
    if not name:
        return []
    variants = [name]
    stem = name[:-1] if name.endswith(("а", "я")) else name
    for suffix in ("очка", "ушка", "онька", "унька", "ечка", "улька", "уся"):
        variants.append(stem + suffix)
    return list(dict.fromkeys(v for v in variants if v))


def build_wakeword_grammar(
    phrase: str,
    *,
    interrupt_mode: bool = False,
    followup_mode: bool = False,
) -> list[str]:
    """Build the finite grammar for one listener state.

    The always-on idle listener only accepts the full configured phrase so a
    random bare ``пура`` in music or PURAH's own speech cannot trigger it.
    Both the short post-answer window (``followup_mode``) and the barge-in
    listener (``interrupt_mode``) accept any spoken variant of the name — the
    user asks for a short, natural way to interrupt: «Пура», «Пурочка»…
    """
    configured = normalize_wake_text(phrase)
    if not configured:
        return []

    phrases = [configured]
    # Vosk commonly confuses the final unstressed vowel in "Пура". Only add the
    # two-word/full configured phrase variant, never a standalone "пора"/"пура".
    words = configured.split()
    if len(words) >= 2 and words[-1] == "пура":
        phrases.append(" ".join(words[:-1] + ["пора"]))

    name = words[-1] if words else "пура"
    if followup_mode:
        # The dialog is still on screen for a short period after an answer. Any
        # spoken variant of the name is a low-friction way to start the next turn.
        phrases.extend(name_variants(name))

    # Close is handled locally by the UI, without an API round-trip.  Include
    # both forms because users naturally omit the greeting in a close command.
    if words:
        phrases.extend(
            [
                f"{configured} закройся",
                f"{name} закройся",
                f"{name} закрой окно",
                f"{name} закрой интерфейс",
                f"{name} выключись",
            ]
        )

    if interrupt_mode:
        # While a response is active any name variant is an intentional barge-in,
        # plus the explicit stop/listen commands.
        phrases.extend(name_variants(name))
        phrases.extend([f"{name} стоп", f"{name} слушай"])
    return list(dict.fromkeys(item for item in phrases if item))


def is_close_command(text: str, phrase: str = "Привет, Пура") -> bool:
    """Return true for a narrowly scoped voice command that closes the overlay.

    We require a spoken variant of the wake name (or its full configured phrase)
    and a close verb; this prevents an ordinary request such as ``закрой
    терминал`` from hiding PURAH.  The helper is pure so the grammar and
    transcription paths share the same behavior and can be tested without Qt,
    Vosk, or a microphone.
    """
    tokens = normalize_wake_text(text).split()
    configured = normalize_wake_text(phrase).split()
    if not tokens or not configured:
        return False
    name = configured[-1]
    spoken_names = set(name_variants(name))
    allowed_prefixes = {tuple(), tuple(configured[:-1])}
    actions = {
        ("закройся",),
        ("скройся",),
        ("выключись",),
        ("закрой", "окно"),
        ("закрой", "интерфейс"),
        ("закрой", name),
    }
    # Allow a polite trailing word, but do not accept arbitrary extra content.
    for index, token in enumerate(tokens):
        if token not in spoken_names:
            continue
        prefix = tuple(tokens[:index])
        if prefix not in allowed_prefixes:
            continue
        rest = tokens[index + 1 :]
        if rest and rest[-1] == "пожалуйста":
            rest = rest[:-1]
        if tuple(rest) in actions:
            return True
    return False


def contains_wake_phrase(text: str, phrases: list[str]) -> bool:
    """Match a complete phrase inside a finalized utterance, not a partial hypothesis."""
    tokens = normalize_wake_text(text).split()
    if not tokens:
        return False
    for phrase in phrases:
        wanted = normalize_wake_text(phrase).split()
        if not wanted or len(wanted) > len(tokens):
            continue
        if any(tokens[i:i + len(wanted)] == wanted for i in range(len(tokens) - len(wanted) + 1)):
            return True
    return False