"""Streaming TTS synthesis and optional Kokoro warmup, independent of UI."""
from __future__ import annotations
import asyncio
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from tts_text import clean_tts_text
from tts_engine_utils import espeak_command, piper_settings

_TTS_SYNTHESIS_LOCK = threading.Lock()

class TTSStreamWorker(QThread):
    """Synthesize short sentence chunks in the background as the LLM streams them."""
    audio_ready = Signal(str)
    failed = Signal(str)
    generation_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._input_finished = False
        self.engine = os.getenv("PURAH_TTS_ENGINE", "edge").strip().casefold()
        self.voice = os.getenv("PURAH_TTS_VOICE", "ru-RU-SvetlanaNeural")
        self.rate = os.getenv("PURAH_TTS_RATE", "+5%")
        self.volume = os.getenv("PURAH_TTS_VOLUME", "+0%")
        self.piper_binary = os.path.expanduser(os.getenv("PURAH_PIPER_BINARY", "piper"))
        self.piper_model = os.path.expanduser(os.getenv("PURAH_PIPER_MODEL", ""))
        self.kokoro_model_dir = os.path.expanduser(os.getenv(
            "PURAH_KOKORO_MODEL_DIR",
            str(Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "purah" / "kokoro-ru"),
        ))
        self.kokoro_voice = os.getenv("PURAH_KOKORO_VOICE", "masha").strip().casefold()
        try:
            self.kokoro_speed = float(os.getenv("PURAH_KOKORO_SPEED", "1.10"))
        except ValueError:
            self.kokoro_speed = 1.10
        try:
            self.kokoro_threads = max(1, min(16, int(os.getenv("PURAH_KOKORO_THREADS", "4"))))
        except ValueError:
            self.kokoro_threads = 4

    def enqueue(self, text: str) -> None:
        text = clean_tts_text(text)
        if text and not self._stop_event.is_set() and not self._input_finished:
            self._queue.put(text)

    def finish_input(self) -> None:
        if self._input_finished:
            return
        self._input_finished = True
        self._queue.put(None)

    def stop(self) -> None:
        self._stop_event.set()
        # Discard not-yet-started sentences; never let stale speech play after Escape.
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        self._queue.put(None)

    async def _synthesize_edge(self, text: str, output_path: str) -> None:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice=self.voice, rate=self.rate, volume=self.volume)
        await communicate.save(output_path)

    def _load_piper_voice(self):
        if not self.piper_model or not Path(self.piper_model).is_file():
            raise RuntimeError(
                "Для TTS engine='piper' укажи существующий tts.piper_model в config.toml. "
                "Для низкой задержки установи локальную модель командой ./setup_piper_tts.sh."
            )
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise RuntimeError("Piper не установлен в .venv. Выполни ./setup_piper_tts.sh.") from exc
        # Load the ONNX model once per response. Spawning the CLI for every sentence
        # reloads a ~63 MB voice repeatedly and destroys the low-latency benefit.
        return PiperVoice.load(self.piper_model)

    def _synthesize_piper(self, voice, text: str, output_path: str) -> None:
        syn_config = None
        try:
            from piper import SynthesisConfig
            length_scale, volume = piper_settings(self.rate, self.volume)
            syn_config = SynthesisConfig(length_scale=length_scale, volume=volume)
        except ImportError:
            # Older piper-tts versions may not expose synthesis controls; synthesis remains usable.
            print("PURAH TTS: Piper SynthesisConfig unavailable; rate/volume settings ignored", file=sys.stderr)
        with wave.open(output_path, "wb") as wav_file:
            if syn_config is None:
                voice.synthesize_wav(text, wav_file)
            else:
                voice.synthesize_wav(text, wav_file, syn_config=syn_config)

    def _synthesize_espeak_ng(self, text: str, output_path: str) -> None:
        binary = shutil.which("espeak-ng")
        if not binary:
            raise RuntimeError("Не найден espeak-ng. Установи пакет espeak-ng (Arch: sudo pacman -S espeak-ng).")
        voice = os.getenv("PURAH_ESPEAK_VOICE", "ru").strip() or "ru"
        result = subprocess.run(
            espeak_command(binary, voice, self.rate, self.volume, output_path),
            input=text,
            text=True,
            encoding="utf-8",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or "").strip()
            raise RuntimeError(f"eSpeak NG завершился с кодом {result.returncode}: {detail or 'ошибка синтеза'}")

    def run(self) -> None:
        try:
            print(
                f"PURAH TTS: worker starting; engine={self.engine!r}; "
                f"voice={self.kokoro_voice if self.engine == 'kokoro_ru' else (os.getenv('PURAH_ESPEAK_VOICE', 'ru') if self.engine == 'espeak_ng' else (self.piper_model if self.engine == 'piper' else self.voice))!r}",
                file=sys.stderr,
                flush=True,
            )
            if self.engine == "none":
                return
            if self.engine not in {"edge", "piper", "espeak_ng", "kokoro_ru"}:
                raise RuntimeError(
                    f"Неизвестный TTS engine={self.engine!r}. Используй 'edge', 'piper', 'espeak_ng', 'kokoro_ru' или 'none'."
                )
            piper_voice = self._load_piper_voice() if self.engine == "piper" else None
            kokoro_engine = None
            if self.engine == "kokoro_ru":
                from kokoro_ru_tts import get_kokoro_ru_engine
                print(
                    f"PURAH TTS: loading Kokoro-ru voice={self.kokoro_voice!r} "
                    f"from {self.kokoro_model_dir}",
                    file=sys.stderr,
                    flush=True,
                )
                kokoro_engine = get_kokoro_ru_engine(
                    model_dir=self.kokoro_model_dir,
                    voice=self.kokoro_voice,
                    threads=self.kokoro_threads,
                )
                print("PURAH TTS: Kokoro-ru engine ready", file=sys.stderr, flush=True)
            while not self._stop_event.is_set():
                try:
                    text = self._queue.get(timeout=0.15)
                except queue.Empty:
                    continue
                if text is None:
                    break
                suffix = ".wav" if self.engine in {"piper", "espeak_ng", "kokoro_ru"} else ".mp3"
                handle = tempfile.NamedTemporaryFile(prefix="purah-tts-", suffix=suffix, delete=False)
                output_path = handle.name
                handle.close()
                keep_path = False
                started = time.monotonic()
                try:
                    with _TTS_SYNTHESIS_LOCK:
                        # Cancellation is checked again after acquiring the lock so a
                        # new request won't synthesize stale speech queued by an older turn.
                        if self._stop_event.is_set():
                            continue
                        if self.engine == "edge":
                            asyncio.run(self._synthesize_edge(text, output_path))
                        elif self.engine == "piper":
                            self._synthesize_piper(piper_voice, text, output_path)
                        elif self.engine == "espeak_ng":
                            self._synthesize_espeak_ng(text, output_path)
                        else:
                            kokoro_engine.synthesize_wav(text, output_path, speed=self.kokoro_speed)
                    if self._stop_event.is_set():
                        continue
                    if not Path(output_path).is_file() or Path(output_path).stat().st_size < 256:
                        raise RuntimeError("TTS не создал аудиофайл или файл слишком мал.")
                    keep_path = True
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    print(f"PURAH TTS: chunk ready in {elapsed_ms} ms ({len(text)} chars)", file=sys.stderr)
                    self.audio_ready.emit(output_path)
                except Exception as exc:
                    if not self._stop_event.is_set():
                        self.failed.emit(str(exc))
                finally:
                    if not keep_path:
                        try:
                            Path(output_path).unlink(missing_ok=True)
                        except OSError:
                            pass
        finally:
            self.generation_finished.emit()


class KokoroWarmupWorker(QThread):
    """Warm the local G2P + ONNX path at startup to reduce first-response latency."""
    ready = Signal(str)
    failed = Signal(str)

    def __init__(self, model_dir: str, voice: str, threads: int, speed: float, parent=None):
        super().__init__(parent)
        self.model_dir = model_dir
        self.voice = voice
        self.threads = threads
        self.speed = speed

    def run(self) -> None:
        started = time.monotonic()
        try:
            from kokoro_ru_tts import get_kokoro_ru_engine
            engine = get_kokoro_ru_engine(
                model_dir=self.model_dir, voice=self.voice, threads=self.threads
            )
            # This first short render also warms RUAccent/G2P and allocates ONNX buffers.
            engine._render_audio("Привет, Пура.", speed=self.speed)
            elapsed = int((time.monotonic() - started) * 1000)
            message = f"Kokoro warmup ready in {elapsed} ms; voice={self.voice}"
            print(f"PURAH TTS: {message}", file=sys.stderr, flush=True)
            self.ready.emit(message)
        except Exception as exc:
            message = f"Kokoro warmup failed: {type(exc).__name__}: {exc}"
            print(f"PURAH TTS: {message}", file=sys.stderr, flush=True)
            self.failed.emit(message)

