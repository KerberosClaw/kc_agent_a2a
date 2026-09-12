import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from discord_party.state import NotReady
from test_native import packet
from test_state import registry


spec = importlib.util.spec_from_file_location('continuity_installer', Path(__file__).resolve().parents[2] / 'scripts/discord_party/install_continuity.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ContinuityInstallCase(unittest.TestCase):
    def test_failed_new_start_restores_pointers_and_hook_without_rolling_back_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); runtime = root / 'runtime'; source = root / 'source'
            (source / 'src/discord_party').mkdir(parents=True)
            (source / 'src/discord_party/example.py').write_text('# synthetic release\n')
            def git(*args):
                return subprocess.run(['git', '-C', str(source), *args], capture_output=True, check=True)
            git('init'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid')
            git('add', '.'); git('commit', '-m', 'test')
            old = runtime / 'releases/old'; (old / '.venv/bin').mkdir(parents=True)
            (old / '.venv/bin/python').touch()
            (runtime / 'current').symlink_to(old)
            (runtime / 'review').mkdir(); review = runtime / 'review/new'; packet(review)
            old_review = runtime / 'review/old'; old_review.mkdir()
            (runtime / 'review/current').symlink_to(old_review)
            (runtime / 'config').mkdir()
            for agent, bot in [('agent_a', '20'), ('agent_b', '30')]:
                (runtime / 'config' / (agent + '.json')).write_text(registry(bot).canonical())
                state = runtime / 'state' / agent; state.mkdir(parents=True)
                db = sqlite3.connect(state / 'party.sqlite3')
                db.executescript('CREATE TABLE events(id TEXT);CREATE TABLE outbox(id TEXT);INSERT INTO events VALUES("preserve");')
                db.commit(); db.close()
            hook = root / 'a2a'; hook.mkdir()
            (hook / 'digest-hook.py').write_text('old hook')
            (hook / 'digest-hook-config.json').write_text('{}')
            config = runtime / 'config/continuity.json'
            config.write_text(json.dumps({'party_runtime': str(runtime), 'runtime': str(hook), 'continuity_root': str(runtime / 'continuity')}))
            original = installer.command; calls = []
            def command(argv):
                args = [str(a) for a in argv]
                if len(args) > 1 and args[1].endswith('manage.py'):
                    calls.append(args)
                    if args[2] == 'start' and args[1] != str(old / 'scripts/discord_party/manage.py'):
                        db = sqlite3.connect(runtime / 'state/agent_a/party.sqlite3')
                        db.execute('INSERT INTO events VALUES("arrived-after-backup")'); db.commit(); db.close()
                        raise NotReady('synthetic new-start failure')
                    return '{}'
                return original(argv)
            with patch.object(installer, 'command', side_effect=command), patch.object(installer, 'load_view', return_value={'approved': True}), patch.object(installer.Path, 'home', return_value=root):
                with self.assertRaises(NotReady):
                    installer.install(config, review, source=source)
            self.assertEqual((runtime / 'current').resolve(), old)
            self.assertEqual((runtime / 'review/current').resolve(), old_review)
            self.assertEqual((hook / 'digest-hook.py').read_text(), 'old hook')
            self.assertEqual((hook / 'digest-hook-config.json').read_text(), '{}')
            db = sqlite3.connect(runtime / 'state/agent_a/party.sqlite3')
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 2); db.close()
            self.assertEqual([call[2] for call in calls], ['stop', 'start', 'stop', 'start'])
            self.assertTrue(all('manage.py' in call[1] for call in calls))


if __name__ == '__main__':
    unittest.main()
