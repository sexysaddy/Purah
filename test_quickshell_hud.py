"""Static contract checks for the Quickshell HUD composition and animations."""
from pathlib import Path
import unittest


QML = Path(__file__).parent / "quickshell" / "purah" / "shell.qml"


class QuickshellHudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qml = QML.read_text(encoding="utf-8")

    def test_layer_shell_contract_remains(self):
        self.assertIn('WlrLayershell.layer: WlrLayer.Overlay', self.qml)
        self.assertIn('WlrLayershell.keyboardFocus: shell.ui.visible ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None', self.qml)
        self.assertNotIn('WlrKeyboardFocus.Exclusive', self.qml)
        self.assertIn('!inputField.activeFocus', self.qml)
        self.assertIn('FocusScope {\n            id: contentRoot', self.qml)
        self.assertIn('mask: Region { item: interactionRegion }', self.qml)

    def test_character_moves_left_with_dialog_layout(self):
        self.assertIn('id: hudGroup', self.qml)
        self.assertIn('id: eyeStack', self.qml)
        self.assertIn('x: 0', self.qml[self.qml.index('id: eyeStack'):])
        self.assertIn('shell.hudLayoutProgress', self.qml[self.qml.index('id: hudGroup'):self.qml.index('IpcHandler')])

    def test_crt_columns_reveal_panel_without_fullscreen_mask(self):
        self.assertIn('dialogTotalColumns: 64', self.qml)
        self.assertIn('id: dialogOpenAnimation', self.qml)
        self.assertIn('id: bubbleViewport', self.qml)
        self.assertIn('clip: true', self.qml[self.qml.index('id: bubbleViewport'):self.qml.index('id: bubbleFrame')])
        self.assertIn('hudGroup.expandedTargetWidth * shell.dialogRevealColumns / shell.dialogTotalColumns', self.qml)
        self.assertNotIn('color: "#030a12"\n                            visible: shell.ui.visible && shell.ui.expanded', self.qml)

    def test_chat_uses_separate_styled_message_bubbles(self):
        self.assertIn('function transcriptEntries(value)', self.qml)
        self.assertIn('function syncTranscript(value)', self.qml)
        self.assertIn('if (source === lastTranscriptText) return', self.qml)
        self.assertIn('model: transcriptModel', self.qml)
        self.assertIn('property bool userMessage: speaker === "user"', self.qml)
        self.assertIn('text: message', self.qml)
        self.assertIn('id: microphoneButton', self.qml)
        self.assertIn('id: closeButton', self.qml)

    def test_idle_blinks_and_speaking_phonemes_are_wired(self):
        for sprite in (
            'purah_blink_open.png', 'purah_blink_half_start.png',
            'purah_blink_closed.png', 'purah_blink_half_end.png',
            'purah_blink_smile_closed.png', 'purah_blink_squint.png',
            'purah_phoneme_neutral.png', 'purah_phoneme_a_ya.png',
            'purah_phoneme_o_yo.png', 'purah_phoneme_transition.png',
        ):
            self.assertIn(sprite, self.qml)
        self.assertIn('function phonemeFrameForCharacter(character)', self.qml)
        self.assertIn('function updateSpeechFrameSequence()', self.qml)
        self.assertIn('running: shell.ui.visible && shell.ui.mode !== "SPEAKING"', self.qml)
        self.assertIn('running: shell.ui.visible && shell.ui.mode === "SPEAKING"', self.qml)
        self.assertIn('id: eyeImage', self.qml)
        self.assertIn('y: 18', self.qml[self.qml.index('id: eyeImage'):])
        self.assertIn('id: companionGlass', self.qml)
        self.assertIn('opacity: 0.40', self.qml[self.qml.index('id: companionGlass'):])
        self.assertIn('Red/cyan RGB-like tears are clipped', self.qml)
        self.assertIn('glitchSliceOneY', self.qml)
        self.assertIn('glitchSliceTwoY', self.qml)
        self.assertNotIn('id: glitchEchoRed', self.qml)
        self.assertNotIn('id: glitchEchoCyan', self.qml)

    def test_dialog_is_compact_and_has_no_fake_visualizer(self):
        self.assertIn('expandedTargetWidth: Math.min(390', self.qml)
        self.assertIn('readonly property real dialogHeight: 238', self.qml)
        self.assertIn('id: companionFrame', self.qml)
        self.assertIn('id: statusPlate', self.qml)
        self.assertIn('property real dialogScanAlpha', self.qml)
        self.assertIn('property real dialogEdgePulse', self.qml)
        self.assertIn('Easing.OutBack', self.qml)
        self.assertIn('function onDialogEdgePulseChanged()', self.qml)
        self.assertIn('x: 24', self.qml)
        self.assertIn('y: contentRoot.height - compactHeight - 18', self.qml)
        self.assertIn('function statusLabelForMode(mode)', self.qml)
        self.assertNotIn('waveformPhase', self.qml)
        self.assertNotIn('waveformTimer', self.qml)
        self.assertNotIn('text: shell.ui.mode || "CONNECTED"', self.qml)
        self.assertNotIn('shell.ui.title || shell.ui.status', self.qml)
        self.assertNotIn('style: Text.Outline', self.qml)

    def test_status_is_short_and_centered_and_close_icon_is_geometric(self):
        self.assertIn('text: shell.statusLabelForMode(shell.ui.mode)', self.qml)
        self.assertIn('horizontalAlignment: Text.AlignHCenter', self.qml)
        self.assertIn('rotation: 45', self.qml)
        self.assertNotIn('ctx.fillStyle = "#050d17"', self.qml)
        self.assertIn('color: "#0b1724"', self.qml)
        self.assertIn('shell.dismissAnimated()', self.qml)
        self.assertIn('Layout.preferredWidth: 25', self.qml)


if __name__ == "__main__":
    unittest.main()
