import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/discord_party/continuity.py'


def run(config, cwd):
    return subprocess.run([sys.executable, str(SCRIPT), '--config', str(config), 'save-commit'],
                          cwd=cwd, capture_output=True, text=True, timeout=30)


class CliErrorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_not_ready_reason_is_shown_so_persona_can_correct(self):
        config = self.root / 'continuity.json'
        config.write_text(json.dumps({'continuity_root': str(self.root / 'state'),
                                      'interactive_roots': {'agent_a': str(self.root / 'elsewhere')}}))
        result = run(config, self.root)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(),
                         'CONTINUITY_NOT_READY NotReady: Run save helper from its registered canonical persona repository')

    def test_other_exception_arguments_stay_out_of_logs(self):
        private = self.root / 'private-transcript-name'
        private.write_text('not a directory')
        config = self.root / 'continuity.json'
        config.write_text(json.dumps({'continuity_root': str(private), 'interactive_roots': {}}))
        result = run(config, self.root)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(), 'CONTINUITY_NOT_READY FileExistsError')
        self.assertNotIn('private-transcript-name', result.stderr)


if __name__ == '__main__':
    unittest.main()
