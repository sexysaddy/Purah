#!/usr/bin/env python3
"""Synthesize and play a short diagnostic phrase with PURAH's effective TTS config.

Usage:
    .venv/bin/python test_tts_playback.py           # test configured engine
    .venv/bin/python test_tts_playback.py --kokoro  # diagnostic-only forced Kokoro test
"""
from __future__ import annotations

import os
import sys
from collections import deque
from pathlib import Path

force_kokoro = "--kokoro" in sys.argv
if force_kokoro:
    sys.argv.remove("--kokoro")

from PySide6.QtCore import QObject, QUrl, QTimer, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication
from main import APP_VERSION, TTSStreamWorker

if force_kokoro:
    # Diagnostic-only override after config loading; never affects normal PURAH runs.
    os.environ["PURAH_TTS_ENGINE"] = "kokoro_ru"
    os.environ["PURAH_TTS_ENABLED"] = "true"


class PlaybackHarness(QObject):
    def __init__(self, app: QApplication, worker: TTSStreamWorker, player: QMediaPlayer):
        super().__init__()
        self.app = app
        self.worker = worker
        self.player = player
        self.pending: deque[str] = deque()
        self.current: str | None = None
        self.generation_done = False
        self.got_audio = False
        self.finished = False

    def cleanup(self, path: str | None) -> None:
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

    @Slot(int, str)
    def fail(self, code: int, message: str) -> None:
        if self.finished:
            return
        self.finished = True
        print(message, file=sys.stderr, flush=True)
        self.player.stop()
        self.cleanup(self.current)
        self.current = None
        while self.pending:
            self.cleanup(self.pending.popleft())
        if self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(5000)
        self.app.exit(code)

    @Slot(str)
    def on_audio(self, path: str) -> None:
        self.got_audio = True
        self.pending.append(path)
        self.play_next()

    @Slot()
    def on_generation_done(self) -> None:
        self.generation_done = True
        self.play_next()

    @Slot()
    def on_worker_finished(self) -> None:
        self.generation_done = True
        self.play_next()

    @Slot(object)
    def on_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.cleanup(self.current)
            self.current = None
            self.play_next()

    @Slot(object, str)
    def on_playback_error(self, error, error_string: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            self.fail(4, f"FAIL: Qt audio playback error: {error_string or error}")

    @Slot(str)
    def on_synthesis_error(self, message: str) -> None:
        self.fail(5, f"FAIL: TTS synthesis error: {message}")

    def play_next(self) -> None:
        if self.finished or self.current is not None:
            return
        while self.pending:
            path = self.pending.popleft()
            if not Path(path).is_file():
                continue
            self.current = path
            print(f"Audio file ready ({Path(path).stat().st_size} bytes); starting playback…", flush=True)
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()
            return
        if self.generation_done:
            if self.got_audio:
                self.fail(0, "PASS: synthesis completed and Qt playback reached EndOfMedia.")
            else:
                self.fail(3, "FAIL: TTS worker finished without producing any audio file.")


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("purah-tts-diagnostic")

    engine = os.getenv("PURAH_TTS_ENGINE", "edge").strip().casefold()
    enabled = os.getenv("PURAH_TTS_ENABLED", "true").strip().casefold() not in {"0", "false", "no", "off"}
    if engine == "kokoro_ru":
        voice = os.getenv("PURAH_KOKORO_VOICE", "masha")
    elif engine == "piper":
        voice = os.getenv("PURAH_PIPER_MODEL", "<piper model not configured>")
    elif engine == "espeak_ng":
        voice = os.getenv("PURAH_ESPEAK_VOICE", "ru")
    else:
        voice = os.getenv("PURAH_TTS_VOICE", "ru-RU-SvetlanaNeural")
    print(f"PURAH {APP_VERSION} TTS diagnostic: enabled={enabled}, engine={engine!r}, voice={voice!r}", flush=True)
    if not enabled or engine == "none":
        print("TTS is disabled in the effective environment/config. Enable [tts].enabled and select an engine.", file=sys.stderr)
        return 2

    worker = TTSStreamWorker()
    player = QMediaPlayer()
    output = QAudioOutput()
    output.setVolume(1.0)
    player.setAudioOutput(output)
    harness = PlaybackHarness(app, worker, player)
    player.mediaStatusChanged.connect(harness.on_status)
    player.errorOccurred.connect(harness.on_playback_error)
    worker.audio_ready.connect(harness.on_audio)
    worker.failed.connect(harness.on_synthesis_error)
    worker.generation_finished.connect(harness.on_generation_done)
    worker.finished.connect(harness.on_worker_finished)

    worker.enqueue("Привет! Я Пура. Это проверка русского голоса и воспроизведения звука.")
    worker.finish_input()
    worker.start()
    QTimer.singleShot(120_000, lambda: harness.fail(6, "FAIL: TTS diagnostic timed out after 120 seconds."))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
