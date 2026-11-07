from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import vision


class VisionTests(unittest.TestCase):
    def test_voice_phrases_are_explicit_screen_requests(self):
        for utterance in (
            "Пура, посмотри на экран",
            "посмотри что на экране",
            "глянь что происходит на экране",
            "прочитай, что на экране",
            "look at my screen",
        ):
            with self.subTest(utterance=utterance):
                self.assertTrue(vision.is_screen_request(utterance))
        self.assertFalse(vision.is_screen_request("открой Firefox"))

    def test_active_window_capture_uses_hyprland_geometry_and_memory_output(self):
        calls = []
        jpeg = b"\xff\xd8" + (b"x" * 256)

        def runner(command, **kwargs):
            calls.append(command)
            if command[:2] == ["hyprctl", "activewindow"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"class": "Firefox", "title": "Test", "at": [10, 20], "size": [900, 600]}),
                    stderr="",
                )
            if command[0] == "grim":
                return SimpleNamespace(returncode=0, stdout=jpeg, stderr=b"")
            raise AssertionError(f"unexpected command: {command}")

        with patch.object(vision.shutil, "which", return_value="/usr/bin/tool"):
            result = vision.capture_screen_jpeg(mode="active_window", jpeg_quality=68, runner=runner)
        self.assertEqual(result, jpeg)
        self.assertEqual(calls[1][0], "grim")
        self.assertIn("10,20 900x600", calls[1])
        self.assertEqual(calls[1][-1], "-")

    def test_refuses_to_capture_purah_as_the_active_window(self):
        def runner(command, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"class": "PURAH", "title": "PURAH", "at": [0, 0], "size": [160, 80]}),
                stderr="",
            )

        with patch.object(vision.shutil, "which", return_value="/usr/bin/tool"):
            with self.assertRaises(vision.VisionCaptureError):
                vision.capture_screen_jpeg(mode="active_window", runner=runner)

    def test_terminal_title_with_project_path_is_not_mistaken_for_purah_window(self):
        calls = []
        jpeg = b"\xff\xd8" + (b"x" * 256)

        def runner(command, **kwargs):
            calls.append(command)
            if command[:2] == ["hyprctl", "activewindow"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"class": "kitty", "title": "~/PURAH/PURAH_0_6", "at": [0, 0], "size": [900, 700]}),
                    stderr="",
                )
            if command[0] == "grim":
                return SimpleNamespace(returncode=0, stdout=jpeg, stderr=b"")
            raise AssertionError(f"unexpected command: {command}")

        with patch.object(vision.shutil, "which", return_value="/usr/bin/tool"):
            result = vision.capture_screen_jpeg(mode="active_window", runner=runner)
        self.assertEqual(result, jpeg)
        self.assertEqual(calls[1][0], "grim")


if __name__ == "__main__":
    unittest.main()
