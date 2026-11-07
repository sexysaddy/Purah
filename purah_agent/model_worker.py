"""Cancelable model request QThread. Model API and image encoder are injected.

No import of main.py: this worker also works when main is run as __main__.
"""
from __future__ import annotations
import os
import threading
import time
from PySide6.QtCore import QThread, Signal
from vision import VisionCaptureError

class ModelWorker(QThread):
    chunk = Signal(str)
    succeeded = Signal(str)
    failed = Signal(str)
    approval_requested = Signal(str, object)
    tool_activity = Signal(str)
    capture_requested = Signal(object)

    def __init__(self, parent=None, *, vision_requested: bool = False, ask_fn=None, image_encoder=None):
        super().__init__(parent)
        self.vision_requested = bool(vision_requested)
        self._ask_fn = ask_fn
        self._image_encoder = image_encoder
        self.final_reasoning_content = ""
        self.cancel_event = threading.Event()
        self._response_lock = threading.Lock()
        self._active_response = None
        self._approval_waiters: list[threading.Event] = []

    def _set_response(self, response) -> None:
        with self._response_lock:
            self._active_response = response

    def cancel(self) -> None:
        self.cancel_event.set()
        with self._response_lock:
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        for event in list(self._approval_waiters):
            event.set()

    def _request_vision_capture(self) -> bytes:
        """Ask the GUI thread to hide PURAH briefly and capture the real foreground window."""
        event = threading.Event()
        result: dict[str, object] = {}
        if self.cancel_event.is_set():
            raise VisionCaptureError("Запрос зрения отменён.")
        self.capture_requested.emit((event, result))
        deadline = time.monotonic() + 12.0
        while not event.wait(0.05):
            if self.cancel_event.is_set():
                raise VisionCaptureError("Запрос зрения отменён.")
            if time.monotonic() >= deadline:
                raise VisionCaptureError("Истекло время ожидания захвата экрана.")
        if self.cancel_event.is_set():
            raise VisionCaptureError("Запрос зрения отменён.")
        if result.get("error"):
            raise VisionCaptureError(str(result["error"]))
        image = result.get("image")
        if not isinstance(image, (bytes, bytearray)) or not image:
            raise VisionCaptureError("Не удалось получить данные скриншота.")
        return bytes(image)

    def _request_approval(self, message: str) -> bool:
        event = threading.Event()
        result = {"approved": False}
        self._approval_waiters.append(event)
        try:
            if self.cancel_event.is_set():
                return False
            self.approval_requested.emit(message, (event, result))
            deadline = time.monotonic() + 180
            while not self.cancel_event.is_set() and time.monotonic() < deadline:
                if event.wait(timeout=0.1):
                    return bool(result["approved"]) and not self.cancel_event.is_set()
            return False
        finally:
            try:
                self._approval_waiters.remove(event)
            except ValueError:
                pass

    def run(self) -> None:
        try:
            if self._ask_fn is None or (self.vision_requested and self._image_encoder is None):
                raise RuntimeError("ModelWorker is missing model/vision dependencies")
            image_data_url = None
            if self.vision_requested:
                if os.getenv("PURAH_VISION_ENABLED", "true").strip().casefold() in {"0", "false", "no", "off"}:
                    raise RuntimeError("Зрение отключено. Включи vision.enabled = true в ~/.config/purah/config.toml.")
                mode = os.getenv("PURAH_VISION_CAPTURE_MODE", "active_window").strip().casefold()
                quality = max(35, min(92, int(os.getenv("PURAH_VISION_JPEG_QUALITY", "72"))))
                self.tool_activity.emit("VISION // CAPTURING ACTIVE WINDOW")
                screenshot = self._request_vision_capture()
                if self.cancel_event.is_set():
                    return
                image_data_url = self._image_encoder(
                    screenshot,
                    max_width=max(480, min(2560, int(os.getenv("PURAH_VISION_MAX_IMAGE_WIDTH", "1280")))),
                    quality=quality,
                )
                self.tool_activity.emit("VISION // ANALYZING FRAME")
            answer = self._ask_fn(
                self.chunk.emit,
                self._request_approval,
                self.tool_activity.emit,
                self.cancel_event,
                self._set_response,
                image_data_url=image_data_url,
                use_vision=self.vision_requested,
                on_final_reasoning=lambda value: setattr(self, "final_reasoning_content", str(value or "")),
            )
            if not self.cancel_event.is_set() and answer:
                self.succeeded.emit(answer)
        except Exception as exc:
            if not self.cancel_event.is_set():
                self.failed.emit(str(exc))

