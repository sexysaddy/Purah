"""Small, dependency-free helpers for lightweight local TTS backends."""
from __future__ import annotations

import re


def _percent(raw: str | None, default: float = 0.0) -> float:
    match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", raw or "")
    return float(match.group(1)) if match else default


def piper_settings(rate: str | None, volume: str | None) -> tuple[float, float]:
    """Return Piper length_scale and volume multiplier from Edge-style percentages."""
    speed = 1.0 + _percent(rate) / 100.0
    speed = max(0.5, min(2.0, speed))
    volume_multiplier = 1.0 + _percent(volume) / 100.0
    volume_multiplier = max(0.0, min(2.0, volume_multiplier))
    return 1.0 / speed, volume_multiplier


def espeak_settings(rate: str | None, volume: str | None) -> tuple[int, int]:
    """Convert Edge-like +5% settings to eSpeak NG WPM and amplitude."""
    rate_text = (rate or "").strip()
    if re.fullmatch(r"\d+", rate_text):
        wpm = int(rate_text)
    else:
        wpm = round(175 * (1.0 + _percent(rate_text) / 100.0))
    amplitude = round(100 * (1.0 + _percent(volume) / 100.0))
    return max(80, min(450, wpm)), max(0, min(200, amplitude))


def espeak_command(binary: str, voice: str, rate: str, volume: str, output_path: str) -> list[str]:
    wpm, amplitude = espeak_settings(rate, volume)
    return [binary, "-v", voice or "ru", "-s", str(wpm), "-a", str(amplitude), "-w", output_path, "--stdin"]
