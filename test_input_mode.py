"""Contract checks for the voice/text input mode (mic mute) feature."""
from pathlib import Path
import unittest


ROOT = Path(__file__).parent
UI = (ROOT / "purah_agent" / "ui.py").read_text(encoding="utf-8")
QML = (ROOT / "quickshell" / "purah" / "shell.qml").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
EXAMPLE = (ROOT / "config.toml.example").read_text(encoding="utf-8")


class InputModeTests(unittest.TestCase):
    def test_config_key_is_wired(self):
        self.assertIn('((ui, "input_mode"), "PURAH_INPUT_MODE")', MAIN)
        self.assertIn('input_mode = "voice"', EXAMPLE)

    def test_wake_listener_is_gated_by_input_mode(self):
        self.assertIn('or self.input_mode != "voice"', UI)
        # three gates: start_wake_listener, _maybe_resume_wakeword, _arm_wake_followup
        self.assertGreaterEqual(UI.count('or self.input_mode != "voice"'), 3)

    def test_set_input_mode_stops_and_restarts_listener(self):
        self.assertIn("def set_input_mode(self, mode: str) -> None:", UI)
        self.assertIn("def _sync_input_mode_ui(self) -> None:", UI)
        self.assertIn('self._set_tray_status("INPUT // TEXT MODE")', UI)
        self.assertIn('self._set_tray_status("INPUT // VOICE MODE")', UI)

    def test_text_mode_opens_dialog_instead_of_microphone(self):
        self.assertIn("def open_text_dialog(self) -> None:", UI)
        self.assertIn('if self.input_mode == "text":\n            # Microphone is muted', UI)
        self.assertIn('if self.input_mode == "text":\n            self.open_text_dialog()', UI)

    def test_tray_has_input_mode_submenu(self):
        self.assertIn('self.mode_menu = self.menu.addMenu("Режим ввода")', UI)
        self.assertIn("QActionGroup", UI)
        self.assertIn("def sync_input_mode(self, mode: str) -> None:", UI)
        self.assertIn('if self.window.input_mode == "text":\n                self.show_dialog()', UI)

    def test_overlay_publishes_input_mode(self):
        self.assertIn('"input_mode": str(self.input_mode),', UI)
        self.assertIn('elif action == "toggle_input_mode":', UI)
        self.assertIn('elif action == "set_input_mode":', UI)

    def test_qml_mic_button_is_a_mute_toggle(self):
        self.assertIn('input_mode: "voice"', QML)
        self.assertIn('readonly property bool micOn: String(shell.ui.input_mode || "voice") === "voice"', QML)
        self.assertIn('onClicked: shell.sendAction("toggle_input_mode")', QML)
        self.assertIn('visible: !microphoneButton.micOn', QML)
        self.assertIn('=== "text") return "РЕЖИМ ТЕКСТА"', QML)

    def test_qml_eye_click_opens_dialog_in_text_mode(self):
        self.assertIn('else if (String(shell.ui.input_mode || "voice") === "text") shell.sendAction("expand")', QML)
        self.assertIn('if (String(shell.ui.input_mode || "voice") === "text") shell.sendAction("expand")', QML)


if __name__ == "__main__":
    unittest.main()
