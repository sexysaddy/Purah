import json
import os
import tempfile
import unittest
from pathlib import Path
from purah_terminal import TerminalManager

class TerminalManagerTests(unittest.TestCase):
    def test_background_does_not_open_terminal_and_keeps_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); sub=root/'sub'; sub.mkdir()
            mgr=TerminalManager(root, state_dir=root/'state')
            first=mgr.run(f'cd {sub} && pwd', mode='background')
            self.assertTrue(first['ok'], first)
            self.assertEqual(Path(first['cwd']), sub.resolve())
            second=mgr.run('pwd', mode='background')
            self.assertTrue(second['ok'], second)
            self.assertEqual(Path(second['cwd']), sub.resolve())
            self.assertIn(str(sub.resolve()), second['output'])
            self.assertFalse((root/'state'/'terminal').exists())

    def test_nonzero_error_is_returned_as_data(self):
        with tempfile.TemporaryDirectory() as td:
            mgr=TerminalManager(Path(td), state_dir=Path(td)/'state')
            result=mgr.run('printf error >&2; exit 7', mode='background')
            self.assertFalse(result['ok'])
            self.assertEqual(result['returncode'], 7)
            self.assertIn('error', result['output'])

if __name__ == '__main__': unittest.main()

class RoutingContractTests(unittest.TestCase):
    def test_background_visible_split_is_documented(self):
        prompt = Path('system_prompt.md').read_text(encoding='utf-8')
        self.assertIn('mode=background', prompt)
        self.assertIn('mode=visible', prompt)
        self.assertIn('read_terminal_output', prompt)

if __name__ == '__main__': unittest.main()
