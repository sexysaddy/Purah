"""Qt/Quickshell presentation layer; worker factories and persistence are injected.

This module intentionally does not import main.py (avoids __main__ import duplication).
"""
from __future__ import annotations
import json
import math
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from PySide6.QtCore import (
    QByteArray, QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF,
    QUrl, QTimer, Qt, Signal, QObject, QEvent, QThread,
)
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QBrush, QFont, QIcon, QKeySequence, QPainter,
    QPainterPath, QPen, QShortcut, QTextCharFormat, QTextCursor,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtNetwork import QLocalServer
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QTextBrowser,
    QLineEdit, QPushButton, QMessageBox, QFrame, QLayout, QMenu, QSystemTrayIcon,
)
from tts_text import extract_tts_segments, clean_tts_text
from vision import capture_screen_jpeg, is_screen_request
from wakeword_logic import is_close_command
APP_DIR = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class UiServices:
    runtime_dir: Callable
    instance_server_name: Callable
    get_history: Callable
    save_message: Callable
    clear_history: Callable
    escape_html: Callable
    model_worker_factory: Callable
    listen_worker_type: type
    wake_worker_type: type
    tts_worker_type: type

class SheikahEye(QWidget):
    """Sheikah crest with restrained blue listening pulses and a single green completion flash."""
    clicked = Signal()
    context_clicked = Signal()

    COLORS = {
        "CONNECTED": "#12c5d6",
        "LISTENING": "#63e8ff",
        "TRANSCRIBING": "#9ad9f5",
        "THINKING": "#ffd166",
        "TYPING": "#8de8f0",
        "SPEAKING": "#8de8f0",
        "ERROR": "#ff7979",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(72, 72)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("PURAH // READY")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._svg_template = (APP_DIR / "assets" / "sheikah_crest.svg").read_text(encoding="utf-8")
        self.mode = "CONNECTED"
        self._success_started: float | None = None
        self._color = self.COLORS["CONNECTED"]
        self.renderer = self._build_renderer(self._color)
        self._pulse_started = time.monotonic()
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(40)
        self.pulse_timer.timeout.connect(self._tick)

    def _build_renderer(self, color: str) -> QSvgRenderer:
        svg = self._svg_template.replace("#12c5d6", color).encode("utf-8")
        return QSvgRenderer(QByteArray(svg), self)

    def _set_color(self, color: str) -> None:
        if color == self._color and self.renderer.isValid():
            return
        self._color = color
        previous = self.renderer
        self.renderer = self._build_renderer(color)
        if previous is not None:
            previous.deleteLater()
        self.update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in self.COLORS else "CONNECTED"
        self.setToolTip(
            "PURAH // LISTENING… Нажми, чтобы отменить запись" if mode == "LISTENING"
            else "PURAH // THINKING…" if mode == "THINKING"
            else "PURAH // SPEAKING…" if mode == "SPEAKING"
            else "Нажми, чтобы начать голосовой запрос"
        )
        if self._success_started is None:
            self._set_color(self.COLORS.get(self.mode, self.COLORS["CONNECTED"]))
        self._pulse_started = time.monotonic()
        if mode in {"LISTENING", "THINKING"} or self._success_started is not None:
            if not self.pulse_timer.isActive():
                self.pulse_timer.start()
        elif self.pulse_timer.isActive():
            self.pulse_timer.stop()
        self.update()

    def pulse_success(self) -> None:
        self._success_started = time.monotonic()
        self._set_color("#80f5a6")
        if not self.pulse_timer.isActive():
            self.pulse_timer.start()
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        if self._success_started is not None and now - self._success_started >= 1.35:
            self._success_started = None
            self._set_color(self.COLORS.get(self.mode, self.COLORS["CONNECTED"]))
            if self.mode not in {"LISTENING", "THINKING"}:
                self.pulse_timer.stop()
        self.update()

    def _opacity(self) -> float:
        now = time.monotonic()
        if self._success_started is not None:
            progress = min(1.0, max(0.0, (now - self._success_started) / 1.35))
            # One slow bright-green pulse, then return to the configured state color.
            triangle = max(0.0, 1.0 - abs(progress * 2.0 - 1.0))
            return 0.30 + 0.70 * triangle
        if self.mode == "LISTENING":
            return 0.42 + 0.58 * (0.5 + 0.5 * math.sin((now - self._pulse_started) * 7.0))
        if self.mode == "THINKING":
            blink = 0.5 + 0.5 * math.sin((now - self._pulse_started) * 4.4)
            return 0.28 + 0.72 * blink
        return 1.0

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.context_clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setOpacity(self._opacity())
        if self.renderer.isValid():
            self.renderer.render(painter, QRectF(2, 2, self.width() - 4, self.height() - 4))
        painter.end()


class SheikahBubble(QFrame):
    """Rounded dialog surface. Closing only this surface must not poison future dialogs."""
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            root = self.parentWidget()
            if root is not None and hasattr(root, "close_interaction"):
                root.close_interaction()
                event.accept()
                return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        root = self.parentWidget()
        if root is not None and not getattr(root, "_shutting_down", False):
            # Window-manager close/Alt+F4 dismisses only the bubble. The small
            # eye remains alive; the next response is allowed to create it again.
            event.ignore()
            if hasattr(root, "dismiss_panel"):
                root.dismiss_panel()
            else:
                self.hide()
            return
        event.accept()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sheikahBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        left, top, right = 5.0, 5.0, float(self.width()) - 5.0
        bottom = h - 28.0
        cx = w / 2.0
        path = QPainterPath()
        path.moveTo(left + 12, top)
        path.lineTo(right - 12, top)
        path.quadTo(right, top, right, top + 12)
        path.lineTo(right, bottom - 12)
        path.quadTo(right, bottom, right - 12, bottom)
        path.lineTo(cx + 13, bottom)
        path.lineTo(cx, h - 5)
        path.lineTo(cx - 13, bottom)
        path.lineTo(left + 12, bottom)
        path.quadTo(left, bottom, left, bottom - 12)
        path.lineTo(left, top + 12)
        path.quadTo(left, top, left + 12, top)
        path.closeSubpath()
        p.setPen(QPen(QColor("#35d8e8"), 1.25))
        p.setBrush(QBrush(QColor(5, 17, 29, 250)))
        p.drawPath(path)
        p.end()


class PurahWindow(QWidget):
    def __init__(self, tray_mode: bool = False, *, services: UiServices):
        super().__init__()
        self.services = services
        self.tray_mode = bool(tray_mode)
        # In tray mode, Quickshell owns the visible layer-shell overlay. Qt remains
        # the voice/API/TTS backend and never maps its ordinary toplevel windows.
        self.layer_overlay = bool(
            os.getenv("PURAH_LAYER_OVERLAY", "0").strip().casefold() in {"1", "true", "yes", "on"}
        )
        self.overlay_visible = False
        self._overlay_last_command_id = ""
        self._overlay_last_publish = 0.0
        self._overlay_publish_pending = False
        self.overlay_state_path = self.services.runtime_dir() / "ui-state.json"
        self.overlay_command_path = self.services.runtime_dir() / "ui-command.json"
        if self.layer_overlay:
            self.services.runtime_dir().mkdir(parents=True, exist_ok=True)
            try:
                old_command = json.loads(self.overlay_command_path.read_text(encoding="utf-8"))
                self._overlay_last_command_id = str(old_command.get("id", ""))
            except (OSError, ValueError, AttributeError):
                pass
        self.tray = None
        self.wake_worker: QThread | None = None
        self._wake_listener_interrupt_mode: bool | None = None
        self._wake_listener_followup_mode = False
        self.wake_followup_deadline = 0.0
        self.wake_followup_timer = QTimer(self)
        self.wake_followup_timer.setSingleShot(True)
        self.wake_followup_timer.timeout.connect(self._expire_wake_followup)
        self.tts_worker: QThread | None = None
        self.tts_temp_path: str | None = None
        self.tts_pending_paths: deque[str] = deque()
        self.tts_playing = False
        self.tts_generation_done = True
        self.tts_stream_active = False
        self.tts_stream_buffer = ""
        self.wakeword_paused = False
        # voice = wake word + recorder active, text = keyboard only (mic fully muted).
        self.input_mode = (
            "text"
            if os.getenv("PURAH_INPUT_MODE", "voice").strip().casefold()
            in {"text", "type", "typing", "keyboard"}
            else "voice"
        )
        self.input_mode_path = self.services.runtime_dir() / "input-mode.json"
        self._load_input_mode()
        self.wake_activation_pending = False
        self.wake_session_active = False
        self.pending_wake_worker = None
        self.pending_wake_audio: tuple[bytes, int] | None = None
        self.wake_error: str | None = None
        self.drag_pos = QPoint()
        self.tts_audio_output = QAudioOutput(self)
        self.tts_audio_output.setVolume(1.0)
        self.tts_player = QMediaPlayer(self)
        self.tts_player.setAudioOutput(self.tts_audio_output)
        self.tts_player.mediaStatusChanged.connect(self._on_tts_media_status)
        self.tts_player.errorOccurred.connect(self._on_tts_playback_error)
        self.expanded = False
        self.mode = "CONNECTED"
        self.model_worker: QThread | None = None
        self.listen_worker: QThread | None = None
        self.pending_listen_request = False
        self.typewriter_parts: list[str] = []
        self.stream_buffer = ""
        self.stream_started = False
        self.stream_complete = False
        self.typewriter_cursor: QTextCursor | None = None
        self.answer_format = QTextCharFormat()
        self.answer_format.setForeground(QColor("#bdf7f3"))
        self.answer_format.setFont(QFont("DejaVu Sans Mono", 10))

        self.setObjectName("purahIndicator")
        self.setWindowTitle("PURAH")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(164, 82)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # The conversation is a separate top-level surface, so the eye stays tiny
        # and Hyprland never blurs/frames a large transparent parent window.
        self.panel = SheikahBubble(self)
        self.panel.setObjectName("purahDialog")
        self.panel.setWindowTitle("PURAH // DIALOG")
        self.panel.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.panel.setMinimumSize(1, 1)
        self.panel.setStyleSheet("""
            QLabel { background: transparent; border: none; }
            QPushButton {
                background: rgba(13, 40, 54, 235); color: #b8f7f8;
                border: 1px solid #2a8997; border-radius: 3px;
                padding: 5px 8px; font: bold 9pt 'DejaVu Sans Mono';
            }
            QPushButton:hover { background: rgba(24, 76, 91, 245); color: #ffffff; }
            QPushButton:disabled { color: #527078; border-color: #244652; }
            QLineEdit { background: rgba(2, 10, 18, 245); color: #efffff;
                border: 1px solid #267d8e; padding: 8px;
                font: 10pt 'DejaVu Sans Mono'; selection-background-color: #147c8a; }
            QLineEdit:focus { border: 1px solid #67f0f1; }
        """)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        panel_layout.setContentsMargins(13, 11, 13, 31)
        panel_layout.setSpacing(7)

        header = QHBoxLayout()
        self.panel_title = QLabel("PURAH // SHEIKAH LINK")
        self.panel_title.setStyleSheet("color:#61e8ef;font:bold 10pt 'DejaVu Sans Mono'; letter-spacing:1px;")
        header.addWidget(self.panel_title)
        header.addStretch(1)
        self.panel_state = QLabel("CONNECTED")
        self.panel_state.setStyleSheet("color:#c3b785;font:9pt 'DejaVu Sans Mono';")
        header.addWidget(self.panel_state)
        self.collapse_btn = QPushButton("—")
        self.collapse_btn.setFixedWidth(30)
        self.collapse_btn.setToolTip("Свернуть диалог")
        self.collapse_btn.clicked.connect(self.collapse_panel)
        header.addWidget(self.collapse_btn)
        self.close_btn = QPushButton("×")
        self.close_btn.setFixedWidth(30)
        self.close_btn.setToolTip("Закрыть PURAH")
        self.close_btn.clicked.connect(self.close_interaction)
        header.addWidget(self.close_btn)
        panel_layout.addLayout(header)

        self.dialog_title = QLabel("SHEIKAH LINK // READY")
        self.dialog_title.setStyleSheet("color:#e8bd72;font:bold 9pt 'DejaVu Sans Mono'; padding:2px;")
        panel_layout.addWidget(self.dialog_title)

        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(False)
        self.log.setReadOnly(True)
        self.log.setStyleSheet("""
            QTextBrowser {
                background: rgba(3, 12, 22, 215); color: #d7eeef;
                border: 1px solid #285d70; border-radius: 3px;
                padding: 9px; font: 10pt 'DejaVu Sans Mono';
                selection-background-color: #147c8a;
            }
        """)
        panel_layout.addWidget(self.log, 1)

        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Или напиши сообщение…")
        self.entry.returnPressed.connect(self.send_message)
        self.entry.setStyleSheet("QLineEdit{background:#020b12;color:#efffff;border:1px solid #267d8e;padding:8px;font:10pt 'DejaVu Sans Mono';} QLineEdit:focus{border:1px solid #67f0f1;}")
        input_row.addWidget(self.entry, 1)
        self.send_btn = QPushButton("SEND ↗")
        self.send_btn.clicked.connect(self.send_message)
        input_row.addWidget(self.send_btn)
        self.memory_btn = QPushButton("CLEAR MEMORY")
        self.memory_btn.setToolTip("Очистить локальную историю диалогов")
        self.memory_btn.clicked.connect(self.forget_memory)
        input_row.addWidget(self.memory_btn)
        panel_layout.addLayout(input_row)
        self.panel.hide()

        self.compact = QWidget(self)
        self.compact.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        compact_layout = QVBoxLayout(self.compact)
        compact_layout.setContentsMargins(1, 1, 1, 1)
        compact_layout.setSpacing(1)
        self.eye = SheikahEye(self.compact)
        self.eye.setFixedSize(56, 56)
        self.eye.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.eye.clicked.connect(self.toggle_listening)
        self.eye.context_clicked.connect(self.toggle_panel)
        compact_layout.addWidget(self.eye, 0, Qt.AlignmentFlag.AlignCenter)
        self.status = QLabel("PURAH // CONNECTED", self.compact)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setFixedHeight(18)
        self.status.setStyleSheet(
            "QLabel { color:#72e8ed; background:rgba(2,12,22,220); "
            "border:1px solid #267d8e; border-radius:3px; "
            "padding:0 3px; font:bold 7pt 'DejaVu Sans Mono'; }"
        )
        compact_layout.addWidget(self.status, 0, Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.compact, 1, Qt.AlignmentFlag.AlignCenter)

        self.geometry_animation = QPropertyAnimation(self.panel, b"geometry", self)
        self.geometry_animation.setDuration(280)
        self.geometry_animation.setEasingCurve(QEasingCurve.Type.OutBack)
        self.geometry_animation.finished.connect(self._geometry_animation_finished)

        self.slide_animation = QPropertyAnimation(self, b"geometry", self)
        self.slide_animation.setDuration(300)
        self.slide_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.typewriter_timer = QTimer(self)
        self.typewriter_timer.timeout.connect(self._typewriter_step)
        self.typewriter_timer.setInterval(max(12, min(28, int(os.getenv("PURAH_TYPEWRITER_INTERVAL_MS", "20")))))

        self.restore_history_preview()
        self.set_mode("CONNECTED")
        self._place_initial_indicator()
        if self.layer_overlay:
            self._publish_overlay_state(immediate=True)
            self.overlay_command_timer = QTimer(self)
            self.overlay_command_timer.setInterval(90)
            self.overlay_command_timer.timeout.connect(self._poll_overlay_command)
            self.overlay_command_timer.start()
            self.overlay_heartbeat_timer = QTimer(self)
            self.overlay_heartbeat_timer.setInterval(1000)
            self.overlay_heartbeat_timer.timeout.connect(lambda: self._publish_overlay_state(immediate=True))
            self.overlay_heartbeat_timer.start()

        shortcut = QShortcut(QKeySequence("Ctrl+Shift+Space"), self)
        shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        shortcut.activated.connect(self.toggle_listening)
        self._shortcut = shortcut
        self.escape_shortcut = QShortcut(QKeySequence("Escape"), self)
        self.escape_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.escape_shortcut.activated.connect(self.close_interaction)

    def _clear_wake_followup(self) -> None:
        """Cancel the short bare-name window when a new turn or close starts."""
        self.wake_followup_deadline = 0.0
        timer = getattr(self, "wake_followup_timer", None)
        if timer is not None:
            timer.stop()

    def _followup_active(self) -> bool:
        deadline = float(getattr(self, "wake_followup_deadline", 0.0) or 0.0)
        if deadline <= 0.0:
            return False
        if time.monotonic() >= deadline:
            self._clear_wake_followup()
            return False
        return True

    def _arm_wake_followup(self) -> None:
        """Allow a bare ``Пура`` only while the completed answer remains visible."""
        if (
            not self.tray_mode
            or self.input_mode != "voice"
            or getattr(self, "_shutting_down", False)
        ):
            return
        visible = bool(self.overlay_visible if self.layer_overlay else (self.panel.isVisible() or self.isVisible()))
        if not visible or self._assistant_turn_busy():
            return
        seconds = max(1.0, min(60.0, float(os.getenv("PURAH_WAKE_FOLLOWUP_SECONDS", "30"))))
        self.wake_followup_deadline = time.monotonic() + seconds
        self.wake_followup_timer.start(int(seconds * 1000))
        self._set_tray_status(f"FOLLOW-UP // СКАЖИ «ПУРА» ({int(seconds)} СЕК)")
        self._maybe_resume_wakeword()

    def _expire_wake_followup(self) -> None:
        self._clear_wake_followup()
        self._maybe_resume_wakeword()

    def start_wake_listener(self, *, interrupt_mode: bool = False, followup_mode: bool = False) -> None:
        """Arm the local wake detector while idle or in interrupt-only barge-in mode."""
        if (
            not self.tray_mode
            or self.wakeword_paused
            or self.input_mode != "voice"
            or getattr(self, "_shutting_down", False)
        ):
            return
        if os.getenv("PURAH_WAKEWORD_ENABLED", "true").strip().casefold() in {"0", "false", "no", "off"}:
            self._set_tray_status("WAKE // DISABLED IN CONFIG")
            return
        current = self.wake_worker
        if current is not None and current.isRunning():
            if (
                self._wake_listener_interrupt_mode == bool(interrupt_mode)
                and self._wake_listener_followup_mode == bool(followup_mode)
            ):
                return
            # PortAudio only permits one input stream in this path. Release the old
            # recognizer first, then restart with a grammar appropriate to this state.
            current.stop()
            current.requestInterruption()
            QTimer.singleShot(
                120,
                lambda mode=bool(interrupt_mode), follow=bool(followup_mode): self.start_wake_listener(
                    interrupt_mode=mode, followup_mode=follow
                ),
            )
            return
        if current is not None and not current.isRunning():
            self.wake_worker = None
        self.wake_error = None
        worker = self.services.wake_worker_type(self, interrupt_mode=interrupt_mode, followup_mode=followup_mode)
        self.wake_worker = worker
        self._wake_listener_interrupt_mode = bool(interrupt_mode)
        self._wake_listener_followup_mode = bool(followup_mode)
        worker.state.connect(self.on_wake_state)
        worker.detected.connect(self.on_wake_detected)
        worker.failed.connect(self.on_wake_error)
        worker.finished.connect(lambda w=worker: self._wake_worker_finished(w))
        if interrupt_mode:
            self._set_tray_status("BARGE-IN // SAY ‘ПРИВЕТ, ПУРА’")
        elif followup_mode:
            self._set_tray_status("FOLLOW-UP // СКАЖИ «ПУРА» (15 СЕК)")
        else:
            self._set_tray_status("WAKE // STARTING")
        worker.start()

    def stop_wake_listener(self, wait_ms: int = 700) -> None:
        worker = self.wake_worker
        if worker is None:
            self._wake_listener_interrupt_mode = None
            self._wake_listener_followup_mode = False
            return
        if worker.isRunning():
            worker.stop()
            worker.requestInterruption()
            worker.wait(wait_ms)
        if not worker.isRunning() and self.wake_worker is worker:
            self.wake_worker = None
            self._wake_listener_interrupt_mode = None
            self._wake_listener_followup_mode = False

    def set_wakeword_paused(self, paused: bool) -> None:
        self.wakeword_paused = bool(paused)
        if self.wakeword_paused:
            self.stop_wake_listener()
            self._set_tray_status("WAKE // PAUSED")
        else:
            self.wake_session_active = False
            self.start_wake_listener()

    def _load_input_mode(self) -> None:
        """Restore the last input mode of this session (runtime dir, not config)."""
        try:
            raw = json.loads(self.input_mode_path.read_text(encoding="utf-8"))
            mode = str(raw.get("mode", "")).strip().casefold()
        except (OSError, ValueError, AttributeError, TypeError):
            return
        if mode in {"voice", "text"}:
            self.input_mode = mode

    def _save_input_mode(self) -> None:
        try:
            self.input_mode_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.input_mode_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"mode": self.input_mode}), encoding="utf-8")
            os.replace(tmp, self.input_mode_path)
        except OSError as exc:
            print(f"PURAH: cannot persist input mode: {exc}", file=sys.stderr, flush=True)

    def set_input_mode(self, mode: str) -> None:
        """Switch between wake-word voice input and keyboard-only text input."""
        normalized = (
            "text"
            if str(mode).strip().casefold() in {"text", "type", "typing", "keyboard"}
            else "voice"
        )
        changed = normalized != self.input_mode
        self.input_mode = normalized
        if changed:
            self._save_input_mode()
        if normalized == "text":
            # Mute means mute: no wake phrase, no barge-in, no follow-up window,
            # and an already running recorder is aborted instead of finishing.
            self._clear_wake_followup()
            self.stop_wake_listener()
            if self.listen_worker is not None and self.listen_worker.isRunning():
                self.listen_worker.stop()
                self.set_mode("CONNECTED", "MIC ARRAY // MUTED")
            self._set_tray_status("INPUT // TEXT MODE")
        else:
            self.wake_session_active = False
            self._set_tray_status("INPUT // VOICE MODE")
            if self.tray_mode and not getattr(self, "_shutting_down", False):
                self.start_wake_listener()
        self._sync_input_mode_ui()

    def _sync_input_mode_ui(self) -> None:
        tray = self.tray
        if tray is not None:
            try:
                tray.sync_input_mode(self.input_mode)
            except Exception:
                pass
        self.set_mode(self.mode)
        # The mic button state must not lag behind a throttled publish.
        self._publish_overlay_state(immediate=True)

    def restart_wake_listener(self) -> None:
        self.stop_wake_listener()
        self.wakeword_paused = False
        self.wake_error = None
        QTimer.singleShot(180, self.start_wake_listener)

    def on_wake_state(self, state: str) -> None:
        self._set_tray_status("WAKE // " + state.upper()[:72])

    def on_wake_error(self, message: str) -> None:
        self.wake_error = message
        self._set_tray_status("WAKE // SETUP REQUIRED")
        if self.tray is not None:
            self.tray.notify("PURAH // Wake word is not ready", message[:400])
        print("PURAH wake word:", message, file=sys.stderr)

    def on_wake_detected(self, recognized_text: str) -> None:
        if self.wake_activation_pending or getattr(self, "_shutting_down", False):
            return
        # Closing PURAH is a local control command. Handle it before creating a
        # recorder/model turn so it never consumes an API round or leaves the
        # overlay listening after it has been dismissed.
        if is_close_command(recognized_text, os.getenv("PURAH_WAKEWORD_PHRASE", "Привет, Пура")):
            self._clear_wake_followup()
            self.close_interaction()
            return
        # Keep the worker alive as a reference until its post-wake audio tail is ready.
        self.pending_wake_worker = self.wake_worker
        self.wake_activation_pending = True
        self.wake_session_active = True
        if self._assistant_turn_busy():
            # A fresh wake phrase during generation/playback is a barge-in: cancel the
            # old response immediately, then open the recorder after Vosk releases mic.
            self._interrupt_current_turn(reason="wake phrase", stop_listener=False)
        self._set_tray_status("WAKE // PHRASE DETECTED")
        QTimer.singleShot(70, self._activate_pending_wake)

    def _wake_worker_finished(self, worker) -> None:
        if self.pending_wake_worker is worker:
            # Copy the Python bytes before deleteLater() destroys the QThread QObject.
            self.pending_wake_audio = getattr(worker, "handoff_audio", None)
            self.pending_wake_worker = None
        if self.wake_worker is worker:
            self.wake_worker = None
            self._wake_listener_interrupt_mode = None
        worker.deleteLater()
        if self.wake_activation_pending and not getattr(self, "_shutting_down", False):
            QTimer.singleShot(70, self._activate_pending_wake)
        elif self.tray_mode and not self.wakeword_paused and not getattr(self, "_shutting_down", False):
            QTimer.singleShot(100, self._maybe_resume_wakeword)

    def _activate_pending_wake(self) -> None:
        if not self.wake_activation_pending or getattr(self, "_shutting_down", False):
            return
        if self.wake_worker is not None and self.wake_worker.isRunning():
            QTimer.singleShot(100, self._activate_pending_wake)
            return
        self.wake_activation_pending = False
        worker = self.pending_wake_worker
        if worker is not None:
            self.pending_wake_audio = getattr(worker, "handoff_audio", None)
            self.pending_wake_worker = None
        # The daemon can remain running after the previous turn. Always reset the
        # old conversation surface before animating a fresh wake-word interaction.
        self.dismiss_panel()
        self.slide_in_and_listen()

    def _set_tray_status(self, status: str) -> None:
        if self.tray is not None:
            try:
                self.tray.update_status(status)
            except Exception:
                pass

    def _assistant_turn_busy(self) -> bool:
        """Whether model output, typewriter text, or speech still belongs to an active turn."""
        model_busy = self.model_worker is not None and self.model_worker.isRunning()
        return bool(
            model_busy or self.typewriter_parts or self.typewriter_timer.isActive()
            or self.tts_playing or self.tts_pending_paths or self.tts_stream_active
        )

    def _maybe_resume_wakeword(self) -> None:
        """Keep a strict wake phrase listener armed during responses for voice barge-in."""
        if (
            not self.tray_mode
            or self.wakeword_paused
            or self.input_mode != "voice"
            or getattr(self, "_shutting_down", False)
        ):
            return
        if self.wake_activation_pending:
            return
        if self.listen_worker is not None and self.listen_worker.isRunning():
            return  # The command recorder owns PortAudio while capturing the user's prompt.
        if self.wake_error is not None:
            return
        busy = self._assistant_turn_busy()
        current = self.wake_worker
        desired_followup = (not busy) and self._followup_active()
        if current is not None and current.isRunning():
            if (self._wake_listener_interrupt_mode == busy
                    and self._wake_listener_followup_mode == desired_followup):
                return
            current.stop()
            current.requestInterruption()
            QTimer.singleShot(120, self._maybe_resume_wakeword)
            return
        if busy:
            self.start_wake_listener(interrupt_mode=True)
            return
        self.wake_session_active = False
        self.start_wake_listener(interrupt_mode=False, followup_mode=desired_followup)

    def _maybe_arm_wake_followup(self) -> None:
        """Arm the short bare-name listener only after text and audio are done."""
        if self._assistant_turn_busy() or self.wake_activation_pending:
            return
        self._arm_wake_followup()

    def _tts_is_enabled(self) -> bool:
        return (
            os.getenv("PURAH_TTS_ENABLED", "true").strip().casefold() not in {"0", "false", "no", "off"}
            and os.getenv("PURAH_TTS_ENGINE", "edge").strip().casefold() != "none"
        )

    def _ensure_tts_stream(self) -> bool:
        if not self._tts_is_enabled():
            return False
        if self.tts_stream_active and self.tts_worker is not None and self.tts_worker.isRunning():
            return True
        # One stream per answer. Do not let a previous request's queued audio leak into this answer.
        if self.tts_worker is not None and self.tts_worker.isRunning():
            self.tts_worker.stop()
            self._stop_worker_now(self.tts_worker, 350)
        self._clear_pending_tts_paths()
        self.tts_stream_buffer = ""
        self.tts_generation_done = False
        self.tts_stream_active = True
        print(
            f"PURAH TTS: answer speech stream started; engine={os.getenv('PURAH_TTS_ENGINE', 'edge')!r}",
            file=sys.stderr,
            flush=True,
        )
        worker = self.services.tts_worker_type(self)
        self.tts_worker = worker
        worker.audio_ready.connect(lambda path, w=worker: self._on_tts_audio_ready(w, path))
        worker.failed.connect(lambda message, w=worker: self._on_tts_worker_failed(w, message))
        worker.generation_finished.connect(lambda w=worker: self._on_tts_generation_finished(w))
        worker.finished.connect(lambda w=worker: self._tts_worker_finished(w))
        worker.start()
        return True

    def _feed_tts_text(self, text: str) -> None:
        if not text or not self._ensure_tts_stream():
            return
        self.tts_stream_buffer += text
        try:
            max_chars = int(os.getenv("PURAH_TTS_CHUNK_CHARS", "220"))
        except ValueError:
            max_chars = 220
        segments, self.tts_stream_buffer = extract_tts_segments(
            self.tts_stream_buffer, final=False, max_chars=max_chars
        )
        worker = self.tts_worker
        if worker is not None:
            for segment in segments:
                worker.enqueue(segment)

    def _finish_tts_input(self, fallback_answer: str = "") -> None:
        """Flush the final partial sentence and tell the background synthesizer to finish."""
        if not self._tts_is_enabled():
            self.tts_stream_buffer = ""
            return
        if not self.tts_stream_active:
            source = fallback_answer.strip()
            if not source or not self._ensure_tts_stream():
                return
            self.tts_stream_buffer = source
        try:
            max_chars = int(os.getenv("PURAH_TTS_CHUNK_CHARS", "220"))
        except ValueError:
            max_chars = 220
        segments, remainder = extract_tts_segments(
            self.tts_stream_buffer, final=True, max_chars=max_chars
        )
        self.tts_stream_buffer = remainder
        if remainder.strip():
            segments.append(clean_tts_text(remainder))
            self.tts_stream_buffer = ""
        worker = self.tts_worker
        if worker is not None:
            for segment in segments:
                worker.enqueue(segment)
            worker.finish_input()
        elif not self.tts_playing and not self.tts_pending_paths:
            self._complete_tts_stream()

    def _on_tts_audio_ready(self, worker, path: str) -> None:
        # Queued Qt signals from a cancelled answer may arrive after a new turn begins.
        if worker is not self.tts_worker or getattr(self, "_shutting_down", False):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
            return
        self._queue_tts_audio(path)

    def _on_tts_worker_failed(self, worker, message: str) -> None:
        if worker is self.tts_worker:
            self._on_tts_failed(message)

    def _queue_tts_audio(self, path: str) -> None:
        if getattr(self, "_shutting_down", False):
            Path(path).unlink(missing_ok=True)
            return
        self.tts_pending_paths.append(path)
        self._play_next_tts_audio()

    def _play_next_tts_audio(self) -> None:
        if getattr(self, "_shutting_down", False) or self.tts_playing:
            return
        while self.tts_pending_paths:
            path = self.tts_pending_paths.popleft()
            if not Path(path).is_file():
                continue
            self.tts_temp_path = path
            self.tts_audio_output.setVolume(1.0)
            self.tts_player.setSource(QUrl.fromLocalFile(path))
            self.tts_playing = True
            self.set_mode("SPEAKING")
            print(f"PURAH TTS: starting playback ({Path(path).stat().st_size} bytes)", file=sys.stderr, flush=True)
            self.tts_player.play()
            return
        if self.tts_generation_done:
            self._complete_tts_stream()

    def _on_tts_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._finish_tts_playback()

    def _on_tts_playback_error(self, error, error_string: str) -> None:
        if error != QMediaPlayer.Error.NoError and self.tts_temp_path:
            self._on_tts_failed(f"Не удалось воспроизвести речь: {error_string}")
            self._finish_tts_playback()

    def _on_tts_failed(self, message: str) -> None:
        self._set_tray_status("TTS // ERROR")
        print("PURAH TTS:", message, file=sys.stderr)
        if self.panel.isVisible():
            self._append_error("Озвучка: " + message)

    def _tts_worker_finished(self, worker) -> None:
        if self.tts_worker is worker:
            self.tts_worker = None
        worker.deleteLater()
        self._maybe_resume_wakeword()

    def _cleanup_tts_file(self) -> None:
        path, self.tts_temp_path = self.tts_temp_path, None
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

    def _clear_pending_tts_paths(self) -> None:
        while self.tts_pending_paths:
            path = self.tts_pending_paths.popleft()
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

    def _on_tts_generation_finished(self, worker=None) -> None:
        if worker is not None and worker is not self.tts_worker:
            return
        self.tts_generation_done = True
        if not self.tts_playing and not self.tts_pending_paths:
            self._complete_tts_stream()

    def _finish_tts_playback(self) -> None:
        # The current audio chunk has ended; immediately play the next synthesized sentence.
        self.tts_playing = False
        self.tts_player.stop()
        self.tts_player.setSource(QUrl())
        self._cleanup_tts_file()
        self._play_next_tts_audio()

    def _complete_tts_stream(self) -> None:
        if self.tts_playing or self.tts_pending_paths or not self.tts_generation_done:
            return
        self.tts_stream_active = False
        self.tts_stream_buffer = ""
        if self.mode == "SPEAKING" and not self.typewriter_timer.isActive() and not self.typewriter_parts:
            self.set_mode("CONNECTED")
        self._maybe_resume_wakeword()
        self._maybe_arm_wake_followup()

    def _interrupt_current_turn(self, *, reason: str = "user interruption", stop_listener: bool = True) -> None:
        """Cancel LLM streaming, typewriter output and TTS immediately for a new prompt."""
        if getattr(self, "_shutting_down", False):
            return
        was_generating = self.stream_started or bool(self.typewriter_parts) or self.mode in {
            "THINKING", "VISION", "TYPING", "SPEAKING"
        }
        # Invalidate worker signal handlers before cancellation; any already-queued
        # chunks from the old request will be ignored after a new request begins.
        model_worker, self.model_worker = self.model_worker, None
        if model_worker is not None:
            model_worker.cancel()

        self.typewriter_timer.stop()
        self.typewriter_parts.clear()
        self.stream_buffer = ""
        self.stream_started = False
        self.stream_complete = False
        self.typewriter_cursor = None
        self.pending_listen_request = False

        tts_worker, self.tts_worker = self.tts_worker, None
        if tts_worker is not None:
            tts_worker.stop()
        # Stop the actual Qt playback before dropping the temporary file. Replacing
        # the source releases its file handle and prevents queued speech leaking later.
        self.tts_player.stop()
        self.tts_player.setSource(QUrl())
        self.tts_playing = False
        self._cleanup_tts_file()
        self._clear_pending_tts_paths()
        self.tts_stream_active = False
        self.tts_generation_done = True
        self.tts_stream_buffer = ""

        self.send_btn.setEnabled(True)
        self.entry.setEnabled(True)
        if stop_listener:
            self.stop_wake_listener(wait_ms=250)
        if was_generating:
            cursor = self.log.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertBlock()
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#ffd166"))
            fmt.setFont(QFont("DejaVu Sans Mono", 9))
            cursor.insertText("[ОТВЕТ ПРЕРВАН — СЛУШАЮ НОВЫЙ ЗАПРОС]", fmt)
            self.log.setTextCursor(cursor)
            self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        self.wake_session_active = True
        self.set_mode("LISTENING", "VOICE LINK // INTERRUPTED — LISTENING")
        if self.layer_overlay:
            self._overlay_set_visible(True, expanded=False)
        else:
            self.show()
            self.raise_()
        self._publish_overlay_state(immediate=True)
        print(f"PURAH: current turn interrupted ({reason}); waiting for new prompt", file=sys.stderr, flush=True)

    def close_interaction(self) -> None:
        """Escape closes a session in tray mode; manual overlay mode exits PURAH fully."""
        if self.tray_mode:
            self.dismiss_to_tray()
        else:
            self.shutdown()

    def dismiss_to_tray(self) -> None:
        """Cancel current work and hide the overlay while leaving the wake listener alive."""
        if getattr(self, "_shutting_down", False):
            return
        self._clear_wake_followup()
        self.wake_activation_pending = False
        self.pending_listen_request = False
        self.typewriter_timer.stop()
        self.geometry_animation.stop()
        self.slide_animation.stop()
        self.stop_wake_listener()
        self._stop_worker_now(self.wake_worker, 800)
        self.wake_worker = None
        if self.listen_worker:
            self.listen_worker.stop()
        if self.model_worker:
            self.model_worker.cancel()
        if self.tts_worker:
            self.tts_worker.stop()
        self.tts_player.stop()
        self.tts_player.setSource(QUrl())
        self.tts_playing = False
        self._cleanup_tts_file()
        self._clear_pending_tts_paths()
        self.tts_stream_active = False
        self.tts_generation_done = True
        self.tts_stream_buffer = ""
        self._stop_worker_now(self.listen_worker, 500)
        self._stop_worker_now(self.model_worker, 500)
        self._stop_worker_now(self.tts_worker, 500)
        self.listen_worker = None
        self.model_worker = None
        self.tts_worker = None
        self.typewriter_parts.clear()
        self.stream_buffer = ""
        self.stream_started = False
        self.stream_complete = False
        self.typewriter_cursor = None
        self.send_btn.setEnabled(True)
        self.entry.setEnabled(True)
        self.dismiss_panel()
        if self.layer_overlay:
            self._overlay_set_visible(False, expanded=False)
        self.hide()
        self.wake_session_active = False
        self.set_mode("CONNECTED")
        self._set_tray_status("WAKE // ARMING")
        QTimer.singleShot(350, self.start_wake_listener)

    def _indicator_geometry(self) -> QRect:
        screen = QApplication.primaryScreen()
        if not screen:
            return QRect(50, 50, self.width(), self.height())
        available = screen.availableGeometry()
        x = available.left() + (available.width() - self.width()) // 2
        bottom_gap = 18
        y = available.bottom() - self.height() + 1 - bottom_gap
        return QRect(x, max(available.top() + 6, y), self.width(), self.height())

    def _place_initial_indicator(self) -> None:
        self.setGeometry(self._indicator_geometry())

    def slide_in_and_listen(self) -> None:
        """Show the interaction eye and immediately start recording."""
        if self.layer_overlay:
            self._overlay_set_visible(True, expanded=False)
            QTimer.singleShot(70, self.start_listening)
            return
        target = self._indicator_geometry()
        screen = QApplication.screenAt(target.center()) or QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            start = QRect(target.x(), available.bottom() + 2, target.width(), target.height())
        else:
            start = QRect(target.x(), target.y() + target.height() + 30, target.width(), target.height())
        self.setGeometry(start)
        self.show()
        self.raise_()
        self.activateWindow()
        self.eye.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.slide_animation.stop()
        self.slide_animation.setStartValue(start)
        self.slide_animation.setEndValue(target)
        self.slide_animation.start()
        QTimer.singleShot(55, self.start_listening)

    def activate_listen_request(self) -> None:
        """Open the eye and start recording; interrupt an answer instead of queueing behind it."""
        if self.input_mode == "text":
            # Microphone is muted: a listen request means "open the dialog for typing".
            self.open_text_dialog()
            return
        if self.listen_worker and self.listen_worker.isRunning():
            if self.layer_overlay:
                self._overlay_set_visible(True)
            return
        if self._assistant_turn_busy():
            # Clicking the eye, invoking the Hyprland bind again, or a tray action
            # should cut off the current answer and immediately accept a new prompt.
            self.wake_activation_pending = True
            self.wake_session_active = True
            self._interrupt_current_turn(reason="listen request", stop_listener=True)
            QTimer.singleShot(70, self._activate_pending_wake)
            return
        if self.tray_mode:
            self.wake_session_active = True
            self.stop_wake_listener()
        self.pending_listen_request = False
        self.dismiss_panel()
        if self.layer_overlay:
            self._overlay_set_visible(True, expanded=False)
        else:
            self.show()
            self.raise_()
            self.activateWindow()
        self.slide_in_and_listen()

    def dismiss_panel(self) -> None:
        """Collapse the dialog; Quickshell owns the visible surface in layer mode."""
        self.expanded = False
        if self.layer_overlay:
            self._publish_overlay_state(immediate=True)
            return
        self.geometry_animation.stop()
        if self.panel.isVisible():
            self.panel.hide()

    def _maybe_run_pending_listen(self) -> None:
        if not self.pending_listen_request or getattr(self, "_shutting_down", False):
            return
        if self.input_mode != "voice":
            self.pending_listen_request = False
            return
        if self.listen_worker and self.listen_worker.isRunning():
            return
        if self.model_worker and self.model_worker.isRunning():
            return
        if self.typewriter_parts or self.typewriter_timer.isActive() or self.tts_playing:
            return
        self.pending_listen_request = False
        QTimer.singleShot(260, lambda: self.start_listening() if not getattr(self, "_shutting_down", False) else None)

    @staticmethod
    def _status_style(mode: str = "CONNECTED") -> str:
        colors = {
            "CONNECTED": ("#58e7ed", "#1a7281"),
            "LISTENING": ("#80eaff", "#258da7"),
            "TRANSCRIBING": ("#a6dff5", "#397c98"),
            "THINKING": ("#ffd166", "#a77e27"),
            "VISION": ("#7ddfff", "#287ca0"),
            "TYPING": ("#91eaf4", "#308a9a"),
            "SPEAKING": ("#8af0b0", "#26854d"),
            "ERROR": ("#ff8d81", "#9b4547"),
        }
        fg, border = colors.get(mode, colors["CONNECTED"])
        return (
            f"QPushButton{{color:{fg};background:rgba(3,13,24,215);border:1px solid {border};"
            "border-radius:3px;padding:3px 7px;font:bold 8pt 'DejaVu Sans Mono';letter-spacing:0.4px;}"
            f"QPushButton:hover{{background:rgba(9,40,54,245);color:#ffffff;}}"
        )

    def restore_history_preview(self) -> None:
        history = self.services.get_history(18)
        self.log.clear()
        if not history:
            self.log.setHtml(
                "<p style='color:#70aeb8'><b>[SHEIKAH LINK ONLINE]</b></p>"
                "<p style='color:#b8d7d8'>Нажми на знак шиика и просто скажи, что нужно сделать.</p>"
            )
            return
        for item in history:
            if item["role"] == "user":
                self.log.append(
                    f"<p style='margin:5px 0;color:#e3bb73'><b>YOU &gt;</b> {self.services.escape_html(item['content'])}</p>"
                )
            else:
                self.log.append(
                    f"<p style='margin:7px 0;color:#bdf7f3'><b>PURAH &gt;</b> {self.services.escape_html(item['content'])}</p>"
                )
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _publish_overlay_state(self, immediate: bool = False) -> None:
        """Publish backend/UI state for Quickshell's WlrLayer.Overlay surface."""
        if not self.layer_overlay:
            return
        now = time.monotonic()
        if not immediate and now - self._overlay_last_publish < 0.065:
            if not self._overlay_publish_pending:
                self._overlay_publish_pending = True
                QTimer.singleShot(70, self._publish_overlay_state_now)
            return
        self._publish_overlay_state_now()

    def _publish_overlay_state_now(self) -> None:
        if not self.layer_overlay:
            return
        self._overlay_publish_pending = False
        payload = {
            "visible": bool(self.overlay_visible),
            "expanded": bool(self.expanded),
            "mode": str(self.mode),
            "status": self.status.text() if hasattr(self, "status") else "PURAH // CONNECTED",
            "title": self.dialog_title.text() if hasattr(self, "dialog_title") else "SHEIKAH LINK // READY",
            "transcript": self.log.toPlainText() if hasattr(self, "log") else "",
            "input_enabled": self.entry.isEnabled() if hasattr(self, "entry") else True,
            "input_mode": str(self.input_mode),
            "heartbeat_ms": int(time.time() * 1000),
        }
        try:
            self.overlay_state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.overlay_state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.overlay_state_path)
            self._overlay_last_publish = time.monotonic()
        except OSError as exc:
            print(f"PURAH overlay: cannot publish UI state: {exc}", file=sys.stderr, flush=True)

    def _poll_overlay_command(self) -> None:
        if not self.layer_overlay or getattr(self, "_shutting_down", False):
            return
        try:
            raw = self.overlay_command_path.read_text(encoding="utf-8")
            command = json.loads(raw)
            command_id = str(command.get("id", ""))
            if not command_id or command_id == self._overlay_last_command_id:
                return
            self._overlay_last_command_id = command_id
            action = str(command.get("action", ""))
            payload = command.get("payload")
        except (OSError, ValueError, AttributeError, TypeError):
            return
        if action == "dismiss":
            self.close_interaction()
        elif action == "listen":
            self.activate_listen_request()
        elif action == "expand":
            self.overlay_visible = True
            self.expand_panel()
        elif action == "cancel":
            if self.listen_worker is not None and self.listen_worker.isRunning():
                self.listen_worker.stop()
                self.set_mode("CONNECTED", "MIC ARRAY // CANCELLING")
            else:
                self.activate_listen_request()
        elif action == "toggle_input_mode":
            self.set_input_mode("text" if self.input_mode == "voice" else "voice")
        elif action == "set_input_mode":
            self.set_input_mode(str(payload or ""))
        elif action == "send_text":
            text = str(payload or "").strip()
            if text:
                self.overlay_visible = True
                self.entry.setText(text)
                self.send_message()
        elif action == "quit":
            self.shutdown()

    def _overlay_set_visible(self, visible: bool, *, expanded: bool | None = None) -> None:
        if not self.layer_overlay:
            return
        self.overlay_visible = bool(visible)
        if expanded is not None:
            self.expanded = bool(expanded)
        self._publish_overlay_state(immediate=True)

    def set_mode(self, mode: str, detail: str | None = None) -> None:
        self.mode = mode
        status_map = {
            "CONNECTED": "PURAH // CONNECTED",
            "LISTENING": "PURAH // LISTENING…",
            "TRANSCRIBING": "PURAH // DECODING…",
            "THINKING": "PURAH // THINKING…",
            "VISION": "PURAH // VISION…",
            "TYPING": "PURAH // RESPONDING…",
            "SPEAKING": "PURAH // SPEAKING…",
            "ERROR": "PURAH // NO CARRIER",
        }
        self.status.setText(status_map.get(mode, status_map["CONNECTED"]))
        self.status.setToolTip(status_map.get(mode, status_map["CONNECTED"]))
        status_colors = {
            "CONNECTED": ("#72e8ed", "#267d8e"),
            "LISTENING": ("#80eaff", "#258da7"),
            "TRANSCRIBING": ("#a6dff5", "#397c98"),
            "THINKING": ("#ffd166", "#a77e27"),
            "TYPING": ("#91eaf4", "#308a9a"),
            "SPEAKING": ("#8af0b0", "#26854d"),
            "ERROR": ("#ff8d81", "#9b4547"),
        }
        status_fg, status_border = status_colors.get(mode, status_colors["CONNECTED"])
        self.status.setStyleSheet(
            "QLabel { color:%s; background:rgba(2,12,22,225); border:1px solid %s; "
            "border-radius:3px; padding:0 3px; font:bold 7pt 'DejaVu Sans Mono'; }"
            % (status_fg, status_border)
        )
        self.panel_state.setText(mode)
        if detail:
            self.dialog_title.setText(detail)
        else:
            self.dialog_title.setText({
                "CONNECTED": "SHEIKAH LINK // READY",
                "LISTENING": "MIC ARRAY // LISTENING",
                "TRANSCRIBING": "SHEIKAH SENSOR // DECODING AUDIO",
                "THINKING": "SHEIKAH LINK // WAITING FOR RESPONSE",
                "VISION": "SHEIKAH SENSOR // CAPTURING ACTIVE WINDOW",
                "TYPING": "INCOMING TRANSMISSION // RECEIVING",
                "SPEAKING": "VOICE LINK // SPEAKING",
                "ERROR": "SHEIKAH LINK // SIGNAL LOST",
            }.get(mode, "SHEIKAH LINK // READY"))
        self.eye.set_mode(mode)
        if mode == "LISTENING":
            self.eye.setToolTip("PURAH // LISTENING… Нажми левую кнопку, чтобы отменить; правую — открыть диалог.")
        elif self.input_mode == "text":
            self.eye.setToolTip(
                f"{status_map.get(mode, status_map['CONNECTED'])} · микрофон выключен · ЛКМ: диалог · ПКМ: диалог"
            )
        else:
            self.eye.setToolTip(f"{status_map.get(mode, status_map['CONNECTED'])} · ЛКМ: голосовой запрос · ПКМ: диалог")
        if self.tray_mode and self.wake_session_active:
            self._set_tray_status(status_map.get(mode, status_map["CONNECTED"]))
        self._publish_overlay_state()

    def _dialog_target_geometry(self, width: int = 460, height: int = 340) -> QRect:
        indicator = self.frameGeometry()
        screen = QApplication.screenAt(indicator.center()) or QApplication.primaryScreen()
        anchor_x = indicator.center().x()
        anchor_y = indicator.top() + 8
        if screen:
            available = screen.availableGeometry()
            width = min(width, max(260, available.width() - 24))
            height = min(height, max(200, available.height() - 24))
            x = max(available.left() + 12, min(anchor_x - width // 2, available.right() - width - 11))
            # The bubble tail ends just above the icon; the indicator remains a separate tiny window.
            y = anchor_y - height + 5
            y = max(available.top() + 12, min(y, available.bottom() - height - 8))
            return QRect(x, y, width, height)
        return QRect(anchor_x - width // 2, anchor_y - height + 5, width, height)

    def _dialog_seed_geometry(self, target: QRect) -> QRect:
        indicator = self.frameGeometry()
        center_x = indicator.center().x()
        anchor_y = indicator.top() + 2
        return QRect(center_x - 7, anchor_y - 8, 14, 12)

    def toggle_panel(self) -> None:
        if self.expanded:
            self.collapse_panel()
        else:
            self.expand_panel()

    def expand_panel(self) -> None:
        if self.layer_overlay:
            self.overlay_visible = True
            self.expanded = True
            self._publish_overlay_state(immediate=True)
            return
        if self.expanded and self.panel.isVisible():
            return
        self.expanded = True
        target = self._dialog_target_geometry(460, 340)
        start = self._dialog_seed_geometry(target)
        self.geometry_animation.stop()
        self.panel.setGeometry(start)
        self.panel.show()
        self.panel.raise_()
        self.activateWindow()
        self.geometry_animation.setStartValue(start)
        self.geometry_animation.setEndValue(target)
        self.geometry_animation.start()

    def collapse_panel(self) -> None:
        if self.layer_overlay:
            self.expanded = False
            self._publish_overlay_state(immediate=True)
            return
        if not self.expanded and not self.panel.isVisible():
            return
        self.expanded = False
        if not self.panel.isVisible():
            return
        start = self.panel.geometry()
        end = self._dialog_seed_geometry(start)
        self.geometry_animation.stop()
        self.geometry_animation.setStartValue(start)
        self.geometry_animation.setEndValue(end)
        self.geometry_animation.start()

    def _geometry_animation_finished(self) -> None:
        if not self.expanded:
            self.panel.hide()

    def open_text_dialog(self) -> None:
        """Show the conversation surface with the input focused, without touching the mic."""
        self.pending_listen_request = False
        if self.layer_overlay:
            self.overlay_visible = True
            self.expanded = True
            self._publish_overlay_state(immediate=True)
            return
        self.show()
        self.raise_()
        self.activateWindow()
        self.expand_panel()
        self.entry.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def toggle_listening(self) -> None:
        if self.input_mode == "text":
            self.open_text_dialog()
            return
        if self.listen_worker and self.listen_worker.isRunning():
            self.listen_worker.stop()
            self.set_mode("CONNECTED", "MIC ARRAY // CANCELLING")
            return
        if (self.model_worker and self.model_worker.isRunning()) or self.typewriter_parts:
            return
        self.start_listening()

    def start_listening(self) -> None:
        if self.input_mode != "voice":
            # Text mode is a hard mute: never open the recorder from a stray signal.
            self.open_text_dialog()
            return
        if self.tray_mode:
            self.wake_session_active = True
            self.stop_wake_listener()
        try:
            import numpy  # noqa: F401
            import sounddevice  # noqa: F401
        except Exception as exc:
            self.expand_panel()
            self.set_mode("ERROR", "MIC ARRAY // DEPENDENCY ERROR")
            self._append_error(
                "Не удалось загрузить аудиозависимости. В активном .venv выполни pip install -r requirements.txt. "
                f"Детали: {exc}"
            )
            return
        self.set_mode("LISTENING")
        initial_audio = self.pending_wake_audio
        self.pending_wake_audio = None
        self.listen_worker = self.services.listen_worker_type(self, initial_audio=initial_audio)
        self.listen_worker.state.connect(self.on_listen_state)
        self.listen_worker.transcribed.connect(self.on_transcribed)
        self.listen_worker.failed.connect(self.on_voice_error)
        self.listen_worker.finished.connect(self.listen_finished)
        self.listen_worker.start()

    def on_listen_state(self, state: str) -> None:
        if "Распознаю" in state:
            self.expand_panel()
            self.set_mode("TRANSCRIBING")
        elif "Слушаю" in state or "микрофон" in state.lower():
            self.set_mode("LISTENING")
        elif "отменена" in state:
            self.set_mode("CONNECTED")

    def on_transcribed(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        # Keep the dialog hidden until the screen-capture step finishes. On Hyprland,
        # activating the Qt dialog first could make PURAH itself the active window.
        self.entry.setText(text)
        self.send_message()

    def on_voice_error(self, message: str) -> None:
        self.expand_panel()
        self.set_mode("ERROR", "MIC ARRAY // INPUT ERROR")
        self._append_error(message)

    def listen_finished(self) -> None:
        self.listen_worker = None
        if self.mode in ("LISTENING", "TRANSCRIBING"):
            self.set_mode("CONNECTED")
        self._maybe_run_pending_listen()
        QTimer.singleShot(320, self._maybe_resume_wakeword)

    def send_message(self) -> None:
        text = self.entry.text().strip()
        if not text or (self.model_worker and self.model_worker.isRunning()) or self.typewriter_parts:
            return
        if self.tray_mode:
            self.wake_session_active = True
            self.stop_wake_listener()
        self.entry.clear()
        self._append_user(text)
        self.services.save_message("user", text)
        self.set_mode("THINKING")
        if self.layer_overlay:
            self.overlay_visible = True
            self.expanded = True
            self._publish_overlay_state(immediate=True)
        else:
            self.expand_panel()
        self.send_btn.setEnabled(False)
        self.entry.setEnabled(False)
        self.stream_buffer = ""
        self.stream_started = False
        self.stream_complete = False
        self.typewriter_parts.clear()
        vision_requested = is_screen_request(text)
        worker = self.services.model_worker_factory(self, vision_requested=vision_requested)
        self.model_worker = worker
        # Ignore late queued signals from a response that was interrupted while it
        # was streaming or while a TTS chunk was being generated.
        worker.chunk.connect(lambda chunk, w=worker: self.on_model_chunk(chunk) if self.model_worker is w else None)
        worker.succeeded.connect(lambda answer, w=worker: self.on_answer(answer, getattr(w, "final_reasoning_content", "")) if self.model_worker is w else None)
        worker.failed.connect(lambda message, w=worker: self.on_error(message) if self.model_worker is w else None)
        worker.finished.connect(lambda w=worker: self.model_finished(w))
        worker.approval_requested.connect(self.handle_tool_approval)
        worker.tool_activity.connect(lambda description, w=worker: self.on_tool_activity(description) if self.model_worker is w else None)
        worker.capture_requested.connect(lambda payload, w=worker: self.on_vision_capture_requested(payload) if self.model_worker is w else self._reject_stale_capture(payload))
        worker.start()

    def on_tool_activity(self, description: str) -> None:
        if "VISION //" in description.upper() or "SCREEN //" in description.upper():
            self.set_mode("VISION", description.upper()[:64])
        else:
            self.set_mode("THINKING", description.upper()[:64])

    @staticmethod
    def _reject_stale_capture(payload) -> None:
        try:
            event, result = payload
            result["error"] = "Запрос зрения отменён до захвата экрана."
            event.set()
        except (TypeError, ValueError, KeyError, AttributeError):
            pass

    def on_vision_capture_requested(self, payload) -> None:
        """Capture the target app without remounting the Quickshell HUD."""
        try:
            event, result = payload
        except (TypeError, ValueError):
            return
        was_eye_visible = self.overlay_visible if self.layer_overlay else self.isVisible()
        was_panel_visible = self.expanded if self.layer_overlay else self.panel.isVisible()
        if self.layer_overlay:
            # Quickshell is a layer-shell HUD, not the active toplevel. Keep it mapped
            # during the focused-window capture: hiding/restoring it makes QML restart
            # the eye/chat entrance animations, which looks like the whole chat reopened.
            # Vision's prompt tells the model to ignore this small HUD in the screenshot.
            QTimer.singleShot(
                0,
                lambda: self._capture_vision(event, result, was_eye_visible, was_panel_visible, restore_ui=False),
            )
            return

        # Preserve the old Qt-window behavior: hide ordinary toplevel windows so that
        # they cannot become the focused target or end up inside the screenshot.
        self.panel.hide()
        self.hide()
        QTimer.singleShot(
            120,
            lambda: self._capture_vision(event, result, was_eye_visible, was_panel_visible, restore_ui=True),
        )

    def _capture_vision(self, event, result, was_eye_visible: bool, was_panel_visible: bool, *, restore_ui: bool) -> None:
        try:
            mode = os.getenv("PURAH_VISION_CAPTURE_MODE", "active_window").strip().casefold()
            quality = max(35, min(92, int(os.getenv("PURAH_VISION_JPEG_QUALITY", "72"))))
            result["image"] = capture_screen_jpeg(mode=mode, jpeg_quality=quality)
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            if restore_ui and not getattr(self, "_shutting_down", False):
                if was_eye_visible:
                    self.show()
                    self.raise_()
                if was_panel_visible:
                    self.panel.show()
                    self.panel.raise_()
            event.set()

    def handle_tool_approval(self, message: str, payload) -> None:
        event, result = payload
        overlay_was_visible = self.overlay_visible if self.layer_overlay else False
        overlay_was_expanded = self.expanded if self.layer_overlay else False
        if self.layer_overlay and overlay_was_visible:
            self._overlay_set_visible(False, expanded=False)
            QApplication.processEvents()
        answer = QMessageBox.question(
            self.panel if self.panel.isVisible() else self,
            "PURAH // РАЗРЕШЕНИЕ ДЕЙСТВИЯ",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        result["approved"] = answer == QMessageBox.StandardButton.Yes
        if self.layer_overlay and overlay_was_visible and not getattr(self, "_shutting_down", False):
            self._overlay_set_visible(True, expanded=overlay_was_expanded)
        event.set()

    def _append_user(self, text: str) -> None:
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertBlock()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#e3bb73"))
        font = QFont("DejaVu Sans Mono")
        font.setBold(True)
        fmt.setFont(font)
        cursor.insertText("YOU > ", fmt)
        fmt2 = QTextCharFormat()
        fmt2.setForeground(QColor("#e8eeee"))
        fmt2.setFont(QFont("DejaVu Sans Mono"))
        cursor.insertText(text, fmt2)
        self.log.setTextCursor(cursor)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        self._publish_overlay_state()

    def _append_error(self, message: str) -> None:
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertBlock()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#ff8d81"))
        fmt.setFont(QFont("DejaVu Sans Mono"))
        cursor.insertText("[PURAH ERROR] " + message, fmt)
        self.log.setTextCursor(cursor)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        self._publish_overlay_state()

    def on_model_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        # Start speech synthesis as soon as a complete sentence is streamed, not after
        # the entire LLM response has arrived. This overlaps LLM generation and TTS.
        self._feed_tts_text(chunk)
        if not self.stream_started:
            self.stream_started = True
            self.expand_panel()
            self.set_mode("TYPING")
            self._append_answer_header()
        self.stream_buffer += chunk
        # Move complete words to the typewriter queue as soon as a whitespace boundary arrives.
        while self.stream_buffer:
            match = re.match(r"\s*\S+\s+", self.stream_buffer)
            if not match:
                break
            part = match.group(0)
            self.typewriter_parts.append(part)
            self.stream_buffer = self.stream_buffer[len(part):]
        if self.typewriter_parts and not self.typewriter_timer.isActive():
            self.typewriter_timer.start()

    def on_answer(self, answer: str, reasoning_content: str = "") -> None:
        answer = answer.strip()
        self.services.save_message("assistant", answer, reasoning_content=reasoning_content)
        self.eye.pulse_success()
        self._finish_tts_input(answer)
        self.stream_complete = True
        if not self.stream_started:
            # The gateway may have returned a non-stream JSON body; reveal that body word-by-word.
            self.stream_started = True
            self.expand_panel()
            self.set_mode("TYPING")
            self._append_answer_header()
            self.typewriter_parts = re.findall(r"\S+\s*", answer)
        elif self.stream_buffer:
            if self.stream_buffer.strip():
                self.typewriter_parts.append(self.stream_buffer)
            self.stream_buffer = ""
        if self.typewriter_parts:
            if not self.typewriter_timer.isActive():
                self.typewriter_timer.start()
        else:
            self.typewriter_timer.stop()
            self.typewriter_cursor = None
            self._typewriter_finished()

    def _append_answer_header(self) -> None:
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertBlock()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#72e8ed"))
        font = QFont("DejaVu Sans Mono")
        font.setBold(True)
        fmt.setFont(font)
        cursor.insertText("PURAH > ", fmt)
        self.typewriter_cursor = cursor
        self.log.setTextCursor(cursor)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _typewriter_step(self) -> None:
        if not self.typewriter_parts:
            self.typewriter_timer.stop()
            if self.stream_complete:
                self.typewriter_cursor = None
                self._typewriter_finished()
            return
        if self.typewriter_cursor is None:
            self.typewriter_timer.stop()
            return
        part = self.typewriter_parts.pop(0)
        self.typewriter_cursor.insertText(part, self.answer_format)
        self.log.setTextCursor(self.typewriter_cursor)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        self._publish_overlay_state()
        if not self.typewriter_parts and self.stream_complete:
            self.typewriter_timer.stop()
            self.typewriter_cursor = None
            self._typewriter_finished()

    def _typewriter_finished(self) -> None:
        """Restore the input state after the final word has been displayed."""
        self.typewriter_timer.stop()
        self.typewriter_cursor = None
        self.stream_buffer = ""
        self.stream_started = False
        self.stream_complete = False
        self.send_btn.setEnabled(True)
        self.entry.setEnabled(True)
        if not self.tts_playing and not self.tts_stream_active and self.mode in {"TYPING", "SPEAKING"}:
            self.set_mode("CONNECTED")
        self._maybe_run_pending_listen()
        QTimer.singleShot(120, self._maybe_resume_wakeword)
        QTimer.singleShot(140, self._maybe_arm_wake_followup)

    def on_error(self, message: str) -> None:
        if self.tts_stream_active:
            self._finish_tts_input()
        self.typewriter_timer.stop()
        self.typewriter_parts.clear()
        self.stream_buffer = ""
        self.stream_complete = True
        self.stream_started = False
        self.typewriter_cursor = None
        self.expand_panel()
        self.set_mode("ERROR", "SHEIKAH LINK // API ERROR")
        self._append_error(message)
        self._maybe_run_pending_listen()

    def model_finished(self, worker=None) -> None:
        if worker is not None and self.model_worker is not worker:
            worker.deleteLater()
            return
        self.model_worker = None
        if worker is not None:
            worker.deleteLater()
        if not self.typewriter_parts:
            self.send_btn.setEnabled(True)
            self.entry.setEnabled(True)
        self._maybe_run_pending_listen()
        QTimer.singleShot(120, self._maybe_resume_wakeword)

    def forget_memory(self) -> None:
        answer = QMessageBox.question(
            self, "Очистить память PURAH?",
            "Удалить сохранённую локальную историю диалогов с этого компьютера? Это нельзя отменить.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.services.clear_history()
            self.log.clear()
            self.log.setHtml(
                "<p style='color:#70aeb8'><b>[SHEIKAH LINK ONLINE]</b></p>"
                "<p style='color:#b8d7d8'>Локальная память очищена.</p>"
            )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.expanded or event.position().y() < 34:
                self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _stop_worker_now(self, worker, timeout_ms: int = 350) -> None:
        if worker is None or not worker.isRunning():
            return
        try:
            if hasattr(worker, "stop"):
                worker.stop()
            elif hasattr(worker, "cancel"):
                worker.cancel()
            worker.requestInterruption()
        except Exception:
            pass
        if worker.wait(timeout_ms):
            return
        # Last resort during explicit app shutdown only. Do not leave Qt threads
        # alive behind a closed overlay or a returned terminal prompt.
        try:
            worker.terminate()
            worker.wait(500)
        except Exception:
            pass

    def shutdown(self) -> None:
        """Close every overlay and stop worker threads; used by Esc and signals."""
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        if self.layer_overlay:
            self.overlay_visible = False
            self.expanded = False
            self._publish_overlay_state(immediate=True)
            if hasattr(self, "overlay_command_timer"):
                self.overlay_command_timer.stop()
            if hasattr(self, "overlay_heartbeat_timer"):
                self.overlay_heartbeat_timer.stop()
        self.typewriter_timer.stop()
        self.geometry_animation.stop()
        self.slide_animation.stop()
        self.stop_wake_listener()
        self._stop_worker_now(self.wake_worker, 800)
        self.wake_worker = None
        if self.listen_worker:
            self.listen_worker.stop()
        if self.model_worker:
            self.model_worker.cancel()
        if self.tts_worker:
            self.tts_worker.stop()
        self.tts_player.stop()
        self.tts_playing = False
        self._cleanup_tts_file()
        self._clear_pending_tts_paths()
        self.tts_stream_active = False
        self.tts_generation_done = True
        self.tts_stream_buffer = ""
        self.panel.hide()
        self.hide()
        self._stop_worker_now(self.listen_worker, 450)
        self._stop_worker_now(self.model_worker, 450)
        self._stop_worker_now(self.tts_worker, 450)
        if self.tray is not None:
            self.tray.hide()
        server = getattr(QApplication.instance(), "_purah_instance_server", None)
        if server is not None:
            try:
                server.close()
                QLocalServer.removeServer(self.services.instance_server_name())
            except Exception:
                pass
        self.panel.close()
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_interaction()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        if not getattr(self, "_shutting_down", False):
            event.ignore()
            self.close_interaction()
            return
        event.accept()


class PurahEscapeFilter(QObject):
    """Make Escape close PURAH even when the line edit or a panel child has focus."""
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            widget = watched if isinstance(watched, QWidget) else None
            while widget is not None:
                if widget is self.window or widget is self.window.panel:
                    self.window.close_interaction()
                    return True
                widget = widget.parentWidget()
        return False


class PurahTray:
    """Small tray controller for always-on wake-word mode."""
    def __init__(self, window: PurahWindow):
        self.window = window
        self.icon = QSystemTrayIcon(QIcon(str(APP_DIR / "assets" / "sheikah_crest.svg")), QApplication.instance())
        self.icon.setToolTip("PURAH // WAKE WORD STARTING")
        self.menu = QMenu()
        self.status_action = QAction("WAKE // STARTING", self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        listen_action = QAction("Слушать сейчас", self.menu)
        listen_action.triggered.connect(self.window.activate_listen_request)
        self.menu.addAction(listen_action)

        show_action = QAction("Открыть диалог", self.menu)
        show_action.triggered.connect(self.show_dialog)
        self.menu.addAction(show_action)

        self.menu.addSeparator()
        self.mode_menu = self.menu.addMenu("Режим ввода")
        self.mode_group = QActionGroup(self.menu)
        self.mode_group.setExclusive(True)
        self.voice_action = QAction("Микрофон (голос)", self.menu)
        self.voice_action.setCheckable(True)
        self.text_action = QAction("Текст (клавиатура)", self.menu)
        self.text_action.setCheckable(True)
        for action, mode in ((self.voice_action, "voice"), (self.text_action, "text")):
            self.mode_group.addAction(action)
            self.mode_menu.addAction(action)
            action.triggered.connect(lambda _checked=False, m=mode: self.window.set_input_mode(m))
        self.menu.addSeparator()

        restart_wake_action = QAction("Перезапустить фразу пробуждения", self.menu)
        restart_wake_action.triggered.connect(self.window.restart_wake_listener)
        self.menu.addAction(restart_wake_action)

        self.menu.addSeparator()
        quit_action = QAction("Полностью выйти из PURAH", self.menu)
        quit_action.triggered.connect(self.window.shutdown)
        self.menu.addAction(quit_action)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.icon.setVisible(True)
        self.window.tray = self
        self.sync_input_mode(self.window.input_mode)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("PURAH: system tray unavailable; wake-word service still runs. Check Waybar tray module.", file=sys.stderr)

    def sync_input_mode(self, mode: str) -> None:
        text_mode = str(mode) == "text"
        self.voice_action.setChecked(not text_mode)
        self.text_action.setChecked(text_mode)

    def update_status(self, status: str) -> None:
        self.icon.setToolTip(("PURAH // " + status)[:120])
        self.status_action.setText(status[:80])

    def notify(self, title: str, message: str) -> None:
        if self.icon.isVisible() and QSystemTrayIcon.supportsMessages():
            self.icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 6500)

    def _activated(self, reason) -> None:
        if reason in {QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick}:
            # Text mode: open the dialog for typing instead of waiting for a mic turn.
            if self.window.input_mode == "text":
                self.show_dialog()
            else:
                self.window.activate_listen_request()

    def show_dialog(self) -> None:
        if self.window.layer_overlay:
            self.window._overlay_set_visible(True, expanded=True)
            return
        self.window.show()
        self.window.raise_()
        self.window.expand_panel()

    def hide(self) -> None:
        self.icon.hide()

