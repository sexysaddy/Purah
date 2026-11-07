"""Privacy-conscious screenshot capture for PURAH on Hyprland/Wayland.

Screenshots are written only to stdout by grim and remain in process memory; this
module never creates a screenshot file on disk.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Callable, Any


class VisionCaptureError(RuntimeError):
    """Raised when PURAH cannot safely capture the requested screen region."""


_SCREEN_REQUESTS = (
    "посмотри на экран",
    "посмотри экран",
    "глянь на экран",
    "глянь экран",
    "посмотри что на экране",
    "посмотри, что на экране",
    "посмотри что у меня на экране",
    "посмотри, что у меня на экране",
    "посмотри на мой экран",
    "что у меня на экране",
    "что на экране",
    "что видишь на экране",
    "что сейчас на экране",
    "что у меня открыто",
    "что сейчас открыто",
    "посмотри активное окно",
    "посмотри на активное окно",
    "прочитай экран",
    "прочитай что на экране",
    "прочитай, что на экране",
    "объясни что на экране",
    "объясни, что на экране",
    "объясни эту ошибку на экране",
    "проанализируй экран",
    "проанализируй это окно",
    "глянь что у меня открыто",
    "глянь, что у меня открыто",
    "глянь что происходит на экране",
    "глянь, что происходит на экране",
    "опиши мой экран",
    "опиши что на экране",
    "look at my screen",
    "what is on my screen",
    "what's on my screen",
    "read my screen",
)


def is_screen_request(text: str) -> bool:
    """Detect an explicit request to inspect the current active application."""
    normalized = " ".join(str(text or "").casefold().replace("ё", "е").split())
    return any(" ".join(phrase.casefold().replace("ё", "е").split()) in normalized for phrase in _SCREEN_REQUESTS)


def _run_json(command: list[str], runner: Callable[..., Any], timeout: float = 2.0) -> Any:
    try:
        result = runner(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise VisionCaptureError(f"Не найдена команда {command[0]}. Установи hyprland/ grim и проверь PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise VisionCaptureError(f"Команда {' '.join(command[:2])} не ответила вовремя.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:500]
        raise VisionCaptureError(f"Команда {' '.join(command[:2])} завершилась с ошибкой: {detail}")
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise VisionCaptureError(f"Команда {' '.join(command[:2])} вернула некорректный JSON.") from exc


def _capture_with_grim(args: list[str], runner: Callable[..., Any], timeout: float = 6.0) -> bytes:
    try:
        result = runner(args, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise VisionCaptureError("Не найдена утилита grim. Установи её командой: sudo pacman -S grim") from exc
    except subprocess.TimeoutExpired as exc:
        raise VisionCaptureError("Захват экрана через grim превысил тайм-аут.") from exc
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", errors="replace").strip()[:600]
        raise VisionCaptureError(f"grim не смог захватить изображение: {detail or 'неизвестная ошибка'}")
    image = bytes(result.stdout or b"")
    if len(image) < 128 or not image.startswith(b"\xff\xd8"):
        raise VisionCaptureError("grim не вернул корректный JPEG. Проверь права захвата экрана и сессию Hyprland.")
    return image


def capture_screen_jpeg(
    *,
    mode: str = "active_window",
    jpeg_quality: int = 72,
    runner: Callable[..., Any] = subprocess.run,
) -> bytes:
    """Capture active window or explicitly selected focused monitor to JPEG bytes.

    The default mode captures the active window only. It deliberately does not
    silently fall back to the whole monitor, which could send unrelated private
    windows to a cloud model.
    """
    if not shutil.which("hyprctl"):
        raise VisionCaptureError("Не найдена команда hyprctl. PURAH должен работать внутри сессии Hyprland.")
    if not shutil.which("grim"):
        raise VisionCaptureError("Не найдена утилита grim. Установи её командой: sudo pacman -S grim")
    quality = max(35, min(92, int(jpeg_quality)))
    if mode == "active_window":
        active = _run_json(["hyprctl", "activewindow", "-j"], runner)
        if not isinstance(active, dict):
            raise VisionCaptureError("Hyprland не сообщил сведения об активном окне.")
        identity = " ".join(str(active.get(key, "")) for key in ("class", "initialClass")).casefold()
        if "purah" in identity:
            raise VisionCaptureError(
                "Активным окном сейчас является сам PURAH. Верни фокус приложению, которое нужно показать, "
                "и повтори фразу «Пура, посмотри на экран». Режим всего монитора можно явно включить в config.toml."
            )
        at = active.get("at")
        size = active.get("size")
        try:
            x, y = int(at[0]), int(at[1])
            width, height = int(size[0]), int(size[1])
        except (TypeError, ValueError, IndexError):
            raise VisionCaptureError("Не удалось определить координаты активного окна в Hyprland.")
        if width < 80 or height < 60 or width > 20000 or height > 20000:
            raise VisionCaptureError(f"Размер активного окна выглядит некорректно: {width}×{height}.")
        geometry = f"{x},{y} {width}x{height}"
        return _capture_with_grim(
            ["grim", "-t", "jpeg", "-q", str(quality), "-g", geometry, "-"], runner
        )

    if mode == "focused_monitor":
        monitors = _run_json(["hyprctl", "monitors", "-j"], runner)
        if not isinstance(monitors, list) or not monitors:
            raise VisionCaptureError("Hyprland не сообщил сведения о мониторах.")
        monitor = next((item for item in monitors if item.get("focused")), monitors[0])
        name = str(monitor.get("name", "")).strip()
        if not name:
            raise VisionCaptureError("Не удалось определить активный монитор.")
        return _capture_with_grim(
            ["grim", "-t", "jpeg", "-q", str(quality), "-o", name, "-"], runner
        )

    raise VisionCaptureError(
        f"Неизвестный vision.capture_mode={mode!r}. Используй 'active_window' или 'focused_monitor'."
    )
