import contextlib
import getpass
import importlib.util
import io
import os
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[2]/'scripts/discord_party/store_tokens.py'
SPEC = importlib.util.spec_from_file_location('store_tokens', FILE)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class TokenCase(unittest.TestCase):
    def test_tokens_are_private_output_redacted_and_rerun_keeps_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)/'secrets'
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch.object(module.getpass, 'getpass', side_effect=['synthetic-one', 'synthetic-two']):
                module.collect(directory)
            self.assertNotIn('synthetic-one', output.getvalue())
            self.assertNotIn('synthetic-two', output.getvalue())
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            for agent, expected in [('agent_a','synthetic-one'),('agent_b','synthetic-two')]:
                target = directory/f'{agent}.token'
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                self.assertEqual(target.read_text(), expected+'\n')
            with contextlib.redirect_stdout(output), patch.object(module.getpass, 'getpass') as prompt:
                module.collect(directory)
                prompt.assert_not_called()

    def test_missing_tty_never_falls_back_to_echoed_input(self):
        def no_tty(_):
            warnings.warn('Cannot control echo', getpass.GetPassWarning, stacklevel=1)
            self.fail('Must abort before reading echoed input')
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), patch.object(module.getpass,'getpass',side_effect=no_tty):
            with self.assertRaises(getpass.GetPassWarning):
                module.collect(Path(tmp)/'secrets')
            self.assertFalse(list(Path(tmp).rglob('*.token')))

    def test_no_overwrite_or_symlink_follow_and_no_partial_tempfiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root/'secrets'
            directory.mkdir(mode=0o700)
            victim = root/'existing'
            victim.write_text('keep')
            (directory/'agent_a.token').symlink_to(victim)
            with self.assertRaises(FileExistsError):
                module.write_token(directory,'agent_a','synthetic')
            self.assertEqual(victim.read_text(),'keep')
            self.assertFalse(list(directory.glob('.incoming-*')))
            linked = root/'linked'
            linked.symlink_to(directory)
            with self.assertRaises(ValueError):
                module.write_token(linked,'agent_a','synthetic')

    def test_invalid_input_and_permissive_directory_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)/'secrets'
            for token in ('', 'two words', 'bad\nvalue', '非ASCII', 'x'*4097):
                with self.assertRaises(ValueError):
                    module.write_token(directory,'agent_a',token)
            directory.mkdir(mode=0o755)
            os.chmod(directory,0o755)
            with self.assertRaises(PermissionError):
                module.write_token(directory,'agent_a','synthetic')
