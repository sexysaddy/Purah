"""Lightweight regression contracts for GUI workflows without requiring Qt/Hyprland."""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from purah_agent.tool_specs import AGENT_TOOLS

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")
UI_SOURCE = (ROOT / "purah_agent/ui.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _extract_function(name: str):
    node = next(
        n for n in TREE.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    module = ast.Module(body=[node], type_ignores=[])
    namespace = {"re": re}
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return namespace[name]


class DesktopAgentTests(unittest.TestCase):
    def test_compound_launch_and_message_requests_bypass_early_return_fastpath(self):
        is_compound = _extract_function("_has_followup_action")
        self.assertTrue(is_compound("Открой сайт ВКонтакте и напиши сообщение Ивану: привет"))
        self.assertTrue(is_compound("Запусти Telegram и отправь в избранное: тест"))
        self.assertTrue(is_compound("Открой терминал и выполни в нём команду ls"))
        self.assertFalse(is_compound("Открой Firefox"))
        self.assertFalse(is_compound("Открой сайт example.org"))

    def test_active_window_tools_are_registered_and_are_filtered_when_disabled(self):
        exposed = {spec["function"]["name"] for spec in AGENT_TOOLS}
        for name in ("inspect_active_window", "type_text_in_active_window",
                     "press_key_in_active_window", "click_in_active_window"):
            self.assertIn(name, exposed)
        self.assertIn('desktop_enabled = os.getenv("PURAH_DESKTOP_CONTROL_ENABLED", "true")', SOURCE)
        self.assertIn('"inspect_active_window", "type_text_in_active_window"', SOURCE)
        self.assertIn('if not desktop_enabled:', SOURCE)

    def test_keyboard_first_atomic_sequence_is_registered(self):
        self.assertIn('desktop_sequence', SOURCE)
        self.assertIn('def tool_desktop_sequence(', SOURCE)
        self.assertIn('DESKTOP_SEQUENCE_LOCK', SOURCE)
        self.assertIn('Ctrl+0', (ROOT / "system_prompt.md").read_text(encoding="utf-8"))
        self.assertIn('coordinate_scale', SOURCE)
        self.assertIn('round(line["left"] / scale_x)', SOURCE)
        self.assertIn('_explicit_terminal_window_request', SOURCE)
        self.assertIn('mode=visible', (ROOT / "system_prompt.md").read_text(encoding="utf-8"))
        terminal_spec = next(spec["function"]["parameters"]["properties"]
                             for spec in AGENT_TOOLS
                             if spec["function"]["name"] == "run_terminal_command")
        self.assertEqual(terminal_spec["mode"]["enum"], ["background", "visible", "auto"])

    def test_agent_has_enough_rounds_for_inspect_type_action_verify(self):
        self.assertIn('min(16, int(os.getenv("PURAH_MAX_TOOL_ROUNDS", "12")))', SOURCE)
        self.assertIn('max_tool_rounds = 12', (ROOT / "config.toml.example").read_text(encoding="utf-8"))

    def test_system_prompt_requires_window_inspection_and_result_verification(self):
        prompt = (ROOT / "system_prompt.md").read_text(encoding="utf-8").casefold()
        self.assertIn("inspect_active_window", prompt)
        self.assertIn("проверь результат", prompt)
        self.assertIn("адресат", prompt)
        self.assertIn("недоверенные данные", prompt)

    def test_wakeword_handoff_preserves_audio_during_recorder_switch(self):
        self.assertIn('handoff_audio: tuple[bytes, int] | None = None', SOURCE)
        self.assertIn('for _ in range(3):', SOURCE)
        self.assertIn('self.pending_wake_audio = getattr(worker, "handoff_audio", None)', UI_SOURCE)
        self.assertIn('self.services.listen_worker_type(self, initial_audio=initial_audio)', UI_SOURCE)
        self.assertIn('prefix_blocks: deque[bytes] = deque()', SOURCE)

    def test_gui_dependencies_are_installed_by_arch_installer(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        for package in ("wtype", "ydotool", "tesseract", "tesseract-data-rus", "tesseract-data-eng"):
            self.assertIn(package, installer)
        self.assertIn('systemctl --user enable --now ydotool.service', installer)
        self.assertIn('Установка продолжится', installer)

    def test_new_defaults_reduce_mouse_and_reasoning_latency(self):
        config = (ROOT / "config.toml.example").read_text(encoding="utf-8")
        self.assertIn('thinking_mode = "disabled"', config)
        self.assertIn('allow_mouse = false', config)


if __name__ == "__main__":
    unittest.main()
