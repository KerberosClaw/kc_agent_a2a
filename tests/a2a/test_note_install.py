import contextlib
import io
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class NoteUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.runtime = self.home/'Library/Application Support/kc-agent-a2a'
        self.runtime.mkdir(parents=True)
        self.helper = self.home/'.local/bin/agent-note'
        self.helper.parent.mkdir(parents=True)
        self.helper.write_text('# old a2a.note_runtime helper\n')
        (self.runtime/'config.json').write_text(json.dumps({'personas': {'agent_a': {'root': str(self.home/'dev/agent_a')}, 'agent_b': {'root': str(self.home/'dev/agent_b')}}}))
        self.config = self.runtime/'note-config.json'
        self.config.write_text(json.dumps({'mail_root': str(self.runtime/'mailbox'), 'helper': str(self.helper),
                                          'sessions': {'existing': {'agent': 'agent_a'}}, 'interactive_roots': {'agent_a': str(self.home/'dev/agent_a'),
                                          'agent_b': str(self.home/'dev/agent_b')}}))
        self.claude = self.home/'.claude/settings.json'; self.claude.parent.mkdir()
        self.codex = self.home/'.codex/config.toml'; self.codex.parent.mkdir()
        command = str(self.helper)+' hook'
        self.claude.write_text(json.dumps({'other': 'preserve', 'hooks': {
            e: [{'hooks': [{'type': 'command', 'command': command}]}] for e in ['UserPromptSubmit','Stop','StopFailure']}}))
        self.codex.write_text('model = "preserve"\n[hooks.state]\ntrusted = "keep"\n'+''.join(
            f'[[hooks.{e}]]\n[[hooks.{e}.hooks]]\ntype = "command"\ncommand = '+json.dumps(command)+'\n'
            for e in ['UserPromptSubmit','Stop','Interrupt']))

    def tearDown(self):
        self.tmp.cleanup()

    def run_upgrade(self):
        script = Path(__file__).resolve().parents[2]/'scripts/install_a2a_notes.py'
        with patch('sys.platform', 'darwin'), patch.object(Path, 'home', return_value=self.home), patch('sys.argv', [str(script), '--update-helper-only']), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                runpy.run_path(str(script), run_name='__main__')
        return error.exception.code

    def test_upgrade_changes_helper_only_preserving_exact_config_bytes(self):
        before = {p: p.read_bytes() for p in [self.claude, self.codex, self.config]}
        self.assertEqual(self.run_upgrade(), 0)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertIn('from a2a.note_runtime import main', self.helper.read_text())
        self.assertEqual(self.helper.stat().st_mode & 0o777, 0o700)

    def test_missing_hook_aborts_before_replacing_helper(self):
        old = self.helper.read_bytes()
        self.codex.write_text('model = "preserve"\n')
        self.assertNotEqual(self.run_upgrade(), 0)
        self.assertEqual(self.helper.read_bytes(), old)


if __name__ == '__main__':
    unittest.main()
