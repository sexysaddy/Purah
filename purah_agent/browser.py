"""Select the user-authorized, focus-free browser automation backend."""
from __future__ import annotations

import os

from .browser_bidi import BrowserBidi
from .browser_cdp import BrowserCdp


class BrowserConfigurationError(RuntimeError):
    pass


def _enabled(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def browser_automation_enabled() -> bool:
    selected = os.getenv("PURAH_BROWSER_AUTOMATION_BACKEND", "auto").strip().casefold()
    firefox = _enabled(os.getenv("PURAH_BROWSER_FIREFOX_BIDI_ENABLED", "false"))
    chromium = _enabled(os.getenv("PURAH_BROWSER_CDP_ENABLED", "false"))
    if selected == "firefox":
        return firefox
    if selected == "chromium":
        return chromium
    return selected == "auto" and (firefox or chromium)


def configured_browser():
    selected = os.getenv("PURAH_BROWSER_AUTOMATION_BACKEND", "auto").strip().casefold()
    if selected not in {"auto", "firefox", "chromium"}:
        raise BrowserConfigurationError("browser.automation_backend должен быть auto, firefox или chromium.")
    firefox = _enabled(os.getenv("PURAH_BROWSER_FIREFOX_BIDI_ENABLED", "false"))
    chromium = _enabled(os.getenv("PURAH_BROWSER_CDP_ENABLED", "false"))
    if selected == "auto":
        default = os.getenv("PURAH_DEFAULT_BROWSER", "firefox").strip().casefold()
        preferred = "firefox" if default in {"firefox", "librewolf"} else "chromium"
        if preferred == "firefox":
            selected = "firefox" if firefox else "chromium"
        else:
            selected = "chromium" if chromium else "firefox"
    if selected == "firefox":
        if not firefox:
            raise BrowserConfigurationError("Firefox BiDi выключен. Установи browser.firefox_bidi_enabled=true.")
        try:
            return BrowserBidi(port=int(os.getenv("PURAH_BROWSER_FIREFOX_BIDI_PORT", "9223")))
        except ValueError as exc:
            raise BrowserConfigurationError("Некорректный browser.firefox_bidi_port.") from exc
    if not chromium:
        raise BrowserConfigurationError("Chromium CDP выключен. Установи browser.cdp_enabled=true.")
    try:
        return BrowserCdp(port=int(os.getenv("PURAH_BROWSER_CDP_PORT", "9222")))
    except ValueError as exc:
        raise BrowserConfigurationError("Некорректный browser.cdp_port.") from exc
