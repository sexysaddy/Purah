"""Resolve provider routing without silently sharing credentials across APIs."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


def same_provider_origin(left: str, right: str) -> bool:
    """Return True only when credentials are safe to reuse across these URLs.

    Compare scheme, hostname and port (not path), so a key is not copied to a
    different service merely because it shares a hostname.
    """
    try:
        a = urlparse(str(left or "").strip())
        b = urlparse(str(right or "").strip())
        return bool(a.scheme and a.hostname and b.scheme and b.hostname) and (
            a.scheme.casefold(), a.hostname.casefold(), a.port
        ) == (b.scheme.casefold(), b.hostname.casefold(), b.port)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class ProviderRoute:
    endpoint: str
    model: str
    api_key: str
    inherited_chat_api: bool


def resolve_vision_route(
    *,
    inherit_api: bool,
    vision_endpoint: str,
    vision_model: str,
    vision_api_key: str,
    chat_endpoint: str,
    chat_model: str,
    chat_api_key: str,
) -> ProviderRoute:
    """Resolve vision settings. Sharing the chat API happens only by explicit opt-in.

    Non-empty per-field vision overrides take priority even when inherit_api is true.
    When inherit_api is false, no text endpoint/model/key is silently borrowed.
    """
    vision_endpoint = str(vision_endpoint or "").strip()
    vision_model = str(vision_model or "").strip()
    vision_api_key = str(vision_api_key or "").strip()
    chat_endpoint = str(chat_endpoint or "").strip()
    chat_model = str(chat_model or "").strip()
    chat_api_key = str(chat_api_key or "").strip()

    if inherit_api:
        endpoint = vision_endpoint or chat_endpoint
        model = vision_model or chat_model
        if vision_api_key:
            api_key = vision_api_key
        elif same_provider_origin(endpoint, chat_endpoint):
            # Reuse the chat key only for the same provider origin. If a custom vision
            # URL points elsewhere, an empty vision key must stay empty, not send a
            # valid but unrelated provider's key and produce a misleading 401.
            api_key = chat_api_key
        else:
            api_key = ""
    else:
        endpoint = vision_endpoint
        model = vision_model
        api_key = vision_api_key

    if not endpoint:
        raise ValueError(
            "Vision API не настроен: укажи [vision].chat_url в ~/.config/purah/config.toml "
            "или явно включи [vision].inherit_api = true, чтобы использовать API текста."
        )
    if not model:
        raise ValueError(
            "Vision API не настроен: укажи [vision].model в ~/.config/purah/config.toml "
            "или явно включи [vision].inherit_api = true."
        )
    return ProviderRoute(endpoint, model, api_key, bool(inherit_api))
