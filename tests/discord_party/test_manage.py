import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


spec = importlib.util.spec_from_file_location(
    'party_manage', Path(__file__).resolve().parents[2] / 'scripts/discord_party/manage.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class ActiveReviewCase(unittest.TestCase):
    def test_restart_uses_selected_version_for_bots_and_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'logs').mkdir()
            (root/'manager.json').write_text(json.dumps({'content':str(root/'custom-content'),'existing_writer_lock':str(root/'custom.lock')}))
            selected = root / 'review/v2'
            selected.mkdir(parents=True)
            (root / 'review/current').symlink_to(selected)
            with patch.object(manager.subprocess, 'Popen', return_value=SimpleNamespace(pid=123456)):
                manager.manage(root, 'start')
            for agent in ('agent_a', 'agent_b', 'archive'):
                argv = json.loads((root / 'run' / (agent + '.json')).read_text())['argv']
                self.assertEqual(argv[argv.index('--grant') + 1], str(selected.resolve()))
                if agent == 'archive':
                    self.assertEqual(argv[argv.index('--content')+1],str(root/'custom-content'))

    def test_broken_selection_does_not_fall_back_and_stop_still_works(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'review/2026-09-09-v1').mkdir(parents=True)
            (root / 'review/current').symlink_to(root / 'review/missing')
            with self.assertRaises(FileNotFoundError):
                manager.manage(root, 'start')
            self.assertEqual(manager.manage(root, 'stop'), {'stopped': True})
