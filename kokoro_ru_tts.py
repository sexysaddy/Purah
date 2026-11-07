"""Lightweight CPU inference for the Russian Kokoro-ru ONNX voice packs.

This module imports optional ML dependencies only when a user selects the
`kokoro_ru` engine. The ONNX session and G2P front end are cached for the life
of the process, so each spoken sentence does not reload the model.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
import wave
from pathlib import Path
from collections import OrderedDict
from typing import Any

_DEFAULT_MODEL_DIR = Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "purah" / "kokoro-ru"
_ENGINE_CACHE: dict[tuple[str, str, int], "KokoroRuEngine"] = {}
_ENGINE_LOCK = threading.Lock()


class KokoroRuError(RuntimeError):
    """Raised for a missing or invalid local Kokoro-ru installation."""


class KokoroRuEngine:
    sample_rate = 24000

    def __init__(self, model_dir: str | Path | None = None, voice: str = "masha", threads: int = 4):
        self.model_dir = Path(os.path.expanduser(str(model_dir or _DEFAULT_MODEL_DIR))).resolve()
        self.voice = str(voice or "masha").strip().casefold()
        if self.voice not in {"masha", "sveta"}:
            if self.voice == "dima":
                raise KokoroRuError("Голос Dima мужской. Для женского голоса Пуры выбери 'masha' или 'sveta'.")
            raise KokoroRuError("Неизвестный голос Kokoro-ru. Доступны женские голоса 'masha' и 'sveta'.")

        model_path = self.model_dir / "onnx" / "model_quantized.onnx"
        config_path = self.model_dir / "config.json"
        g2p_config_path = self.model_dir / "kokoro-config.json"
        g2p_path = self.model_dir / "ru_g2p.py"
        voice_path = self.model_dir / "voices" / f"{self.voice}.bin"
        missing = [str(p) for p in (model_path, config_path, g2p_config_path, g2p_path, voice_path) if not p.is_file()]
        if missing:
            raise KokoroRuError(
                "Файлы Kokoro-ru не установлены. Выполни ./setup_kokoro_ru.sh.\nОтсутствуют: "
                + ", ".join(missing[:4])
            )

        try:
            import numpy as np
            import onnxruntime as ort
        except ImportError as exc:
            raise KokoroRuError(
                "Для Kokoro-ru нужны onnxruntime и numpy. Выполни ./setup_kokoro_ru.sh."
            ) from exc

        self.np = np
        # Repeated short replies (e.g. confirmations) should not re-run RUAccent/G2P.
        # Keep this small and bounded so a long session cannot grow RAM indefinitely.
        self._g2p_lock = threading.Lock()
        self._phoneme_cache: OrderedDict[str, tuple[str, bool]] = OrderedDict()
        self._phoneme_cache_limit = 128
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            self.vocab: dict[str, int] = {str(k): int(v) for k, v in cfg["vocab"].items()}
        except Exception as exc:
            raise KokoroRuError(f"Не удалось прочитать словарь Kokoro-ru ({config_path}): {exc}") from exc

        try:
            self.style = np.fromfile(voice_path, dtype=np.float32).reshape(510, 256)
        except Exception as exc:
            raise KokoroRuError(f"Голосовой пакет Kokoro-ru повреждён ({voice_path}): {exc}") from exc

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.intra_op_num_threads = max(1, min(16, int(threads)))
        opts.inter_op_num_threads = 1
        # Avoid burning CPU while the assistant waits for speech; ONNX remains a CPU-only backend.
        try:
            opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
            opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
        except Exception:
            pass
        try:
            self.session = ort.InferenceSession(
                str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
            )
        except Exception as exc:
            raise KokoroRuError(f"Не удалось загрузить ONNX-модель Kokoro-ru: {exc}") from exc

        # Load the repository's Russian G2P module from the downloaded model
        # directory. Do not import it from arbitrary current-working directories.
        try:
            sys.path.insert(0, str(self.model_dir))
            module_name = "_purah_kokoro_ru_g2p"
            spec = importlib.util.spec_from_file_location(module_name, g2p_path)
            if spec is None or spec.loader is None:
                raise ImportError("Python не смог загрузить ru_g2p.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            self.g2p = module.RuG2P()
        except Exception as exc:
            raise KokoroRuError(
                "Не удалось запустить русскую фонемизацию Kokoro-ru. "
                "Выполни ./setup_kokoro_ru.sh: нужны misaki==0.9.4, espeakng-loader, "
                "phonemizer-fork, espeak-data/ и kokoro-config.json в каталоге модели. "
                f"Подробности: {type(exc).__name__}: {exc}"
            ) from exc

    @staticmethod
    def _split_long_text(text: str, max_chars: int = 145) -> list[str]:
        """Split very long text before G2P; the ONNX voice pack supports <=510 IPA chars."""
        result: list[str] = []
        text = " ".join(str(text).split())
        while len(text) > max_chars:
            limit = min(max_chars, len(text) - 1)
            cut = max(
                text.rfind(mark, 1, limit)
                for mark in (". ", "! ", "? ", "; ", ": ", ", ", " ")
            )
            if cut < max_chars // 2:
                cut = text.rfind(" ", 1, limit)
            if cut < 1:
                cut = limit
            if text[cut:cut + 2] in {". ", "! ", "? ", "; ", ": ", ", "}:
                cut += 1
            part = text[:cut].strip()
            if part:
                result.append(part)
            text = text[cut:].strip()
        if text:
            result.append(text)
        return result

    def _phonemize(self, text: str) -> tuple[str, bool, bool]:
        """Return IPA, OOV flag, and cache-hit status; cache is bounded to 128 phrases."""
        key = " ".join(str(text).split())
        if not key:
            return "", False, False
        try:
            # RUAccent/G2P shares mutable model state, so hold one lock for both cache
            # access and G2P inference. This also prevents warmup/TTS races.
            with self._g2p_lock:
                cached = self._phoneme_cache.get(key)
                if cached is not None:
                    self._phoneme_cache.move_to_end(key)
                    return cached[0], cached[1], True
                ipa, oov = self.g2p(key)
                result = (str(ipa or ""), bool(oov))
                self._phoneme_cache[key] = result
                self._phoneme_cache.move_to_end(key)
                while len(self._phoneme_cache) > self._phoneme_cache_limit:
                    self._phoneme_cache.popitem(last=False)
                return result[0], result[1], False
        except Exception as exc:
            raise KokoroRuError(f"Ошибка русского G2P для текста: {exc}") from exc

    def _render_audio(self, text: str, speed: float):
        np = self.np
        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32)
        g2p_started = time.monotonic()
        ipa, oov, phoneme_cache_hit = self._phonemize(text)
        g2p_ms = int((time.monotonic() - g2p_started) * 1000)
        ipa = str(ipa or "")
        if not ipa:
            raise KokoroRuError("Русский G2P не смог преобразовать текст в фонемы.")
        # The official ONNX pack uses the length of the IPA string to choose a
        # style vector. If the phrase is too long, render smaller pieces.
        if len(ipa) > 509:
            parts = self._split_long_text(text, 125)
            if len(parts) <= 1:
                raise KokoroRuError("Фраза слишком длинная для одного фрагмента Kokoro-ru.")
            audio_parts = [self._render_audio(part, speed) for part in parts]
            silence = np.zeros(int(self.sample_rate * 0.075), dtype=np.float32)
            joined = []
            for i, audio in enumerate(audio_parts):
                if i:
                    joined.append(silence)
                if audio.size:
                    joined.append(audio)
            return np.concatenate(joined) if joined else np.zeros(0, dtype=np.float32)

        token_ids = [self.vocab[ch] for ch in ipa if ch in self.vocab]
        if not token_ids:
            raise KokoroRuError("В фонемах нет символов, известных словарю модели Kokoro-ru.")
        if oov:
            # Keep the text intelligible where possible, but explain that some
            # rare words may be pronounced less accurately instead of silently
            # dropping the whole phrase.
            print(f"PURAH Kokoro-ru: G2P reported out-of-vocabulary items for {text[:100]!r}", file=sys.stderr)
        style_index = len(ipa) - 1
        if not 0 <= style_index < len(self.style):
            raise KokoroRuError(f"Длина фонем ({len(ipa)}) выходит за предел модели Kokoro-ru.")
        inputs = {
            "input_ids": np.asarray([[0, *token_ids, 0]], dtype=np.int64),
            "style": self.style[style_index][None, :],
            "speed": np.asarray([max(0.7, min(1.3, float(speed)))], dtype=np.float32),
        }
        onnx_started = time.monotonic()
        try:
            outputs = self.session.run(None, inputs)
            audio = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        except Exception as exc:
            raise KokoroRuError(f"Ошибка синтеза Kokoro-ru ONNX: {exc}") from exc
        onnx_ms = int((time.monotonic() - onnx_started) * 1000)
        audio_seconds = float(audio.size) / float(self.sample_rate) if self.sample_rate else 0.0
        rtf = (onnx_ms / 1000.0 / audio_seconds) if audio_seconds > 0 else 0.0
        print(
            f"PURAH Kokoro timing: g2p={g2p_ms}ms ({'cache' if phoneme_cache_hit else 'run'}), "
            f"onnx={onnx_ms}ms, audio={audio_seconds:.2f}s, rtf={rtf:.2f}",
            file=sys.stderr,
            flush=True,
        )
        return np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)

    def synthesize_wav(self, text: str, output_path: str | Path, speed: float = 1.0) -> None:
        np = self.np
        parts = self._split_long_text(text, 145)
        if not parts:
            raise KokoroRuError("Пустой текст для синтеза.")
        # Every normal segment is synthesized separately, allowing PURAH to start
        # playback as soon as its first complete sentence has been generated.
        audio_parts = [self._render_audio(part, speed) for part in parts]
        silence = np.zeros(int(self.sample_rate * 0.075), dtype=np.float32)
        joined: list[Any] = []
        for idx, audio in enumerate(audio_parts):
            if idx and audio.size:
                joined.append(silence)
            if audio.size:
                joined.append(audio)
        audio = np.concatenate(joined) if joined else np.zeros(0, dtype=np.float32)
        if not audio.size:
            raise KokoroRuError("Kokoro-ru вернул пустую аудиодорожку.")
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2", copy=False)
        out = Path(output_path)
        with wave.open(str(out), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm.tobytes())


def get_kokoro_ru_engine(model_dir: str | Path | None = None, voice: str = "masha", threads: int = 4) -> KokoroRuEngine:
    key = (str(Path(os.path.expanduser(str(model_dir or _DEFAULT_MODEL_DIR))).resolve()), str(voice).casefold(), max(1, min(16, int(threads))))
    with _ENGINE_LOCK:
        engine = _ENGINE_CACHE.get(key)
        if engine is None:
            engine = KokoroRuEngine(key[0], key[1], key[2])
            _ENGINE_CACHE[key] = engine
        return engine
