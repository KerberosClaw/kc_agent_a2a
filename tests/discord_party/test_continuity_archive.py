import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from a2a.social import git
from discord_party.continuity import Continuity
from discord_party.continuity_worker import export, write_experiences
from discord_party.native import Grant, write_private
from test_native import packet
from test_state import registry
from test_continuity import message, summary
from test_continuity_save import repository


@unittest.skipUnless(shutil.which('git-crypt'), 'git-crypt required')
class ContinuityArchiveCase(unittest.TestCase):
    def test_room_experience_view_is_bounded_and_excludes_withdrawn_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest = packet(root / 'grant')
            grant = Grant(root / 'grant', 'agent_a', registry())
            state = Continuity(root / 'state')
            try:
                for i in range(12):
                    msg = dict(message(i), created_at='2026-09-10T00:00:00Z')
                    state.ingest([msg], now=i*10)
                    job = state.reserve('agent_a', grant.version, force=True, now=i*10+1)
                    state.publish(job, summary(job), now=i*10+2)
                write_experiences(state, root, {'agent_a': grant})
                before = json.loads((root / 'experiences/agent_a.json').read_text())
                self.assertEqual(len(before['summaries']), 8)
                self.assertEqual(before['grant_version'], grant.version)
                self.assertLess(len(json.dumps(before)), 7500)
                self.assertEqual(len(state.unread('agent_a')), 12)  # room use is not main readback
                manifest['approved_at'] = '2026-09-11T00:00:00Z'
                write_private(root / 'grant/manifest.json', json.dumps(manifest))
                new_grant = Grant(root / 'grant', 'agent_a', registry())
                write_experiences(state, root, {'agent_a': new_grant})
                after = json.loads((root / 'experiences/agent_a.json').read_text())
                self.assertEqual(after['summaries'], [])
            finally:
                state.close()

    def test_encrypted_export_recovery_does_not_skip_dirty_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); content = root / 'content'; repository(content)
            remote = root / 'remote.git'
            subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
            git(content, 'remote', 'add', 'origin', str(remote))
            lock = root / 'writer.lock'; lock.touch()
            config = {'content': str(content), 'continuity_root': str(root / 'state'),
                      'party_runtime': str(root / 'runtime'), 'existing_writer_lock': str(lock)}
            state = Continuity(root / 'state')
            try:
                state.ingest([message(1)], now=1)
                job = state.reserve('agent_a', 'v1', force=True, now=2); state.publish(job, summary(job), now=3)
                export(state, config)
                self.assertTrue(git(content, 'show', 'HEAD:continuity/discord/sources.jsonl').startswith(b'\x00GITCRYPT\x00'))
                head = git(content, 'rev-parse', 'HEAD')
                export(state, config); self.assertEqual(head, git(content, 'rev-parse', 'HEAD'))
                state.ingest([message(2)], now=4)
                def fail_commit(root, *args):
                    if args[0] == 'commit':
                        raise RuntimeError('crash after staging')
                    return git(root, *args)
                with patch('discord_party.continuity_worker.git', side_effect=fail_commit):
                    with self.assertRaises(RuntimeError):
                        export(state, config)
                self.assertEqual(head, git(content, 'rev-parse', 'HEAD'))
                export(state, config)
                self.assertNotEqual(head, git(content, 'rev-parse', 'HEAD'))
                self.assertEqual(len((content / 'continuity/discord/sources.jsonl').read_text().splitlines()), 2)
                self.assertEqual(git(content, 'status', '--porcelain'), b'')
                self.assertTrue(json.loads((root / 'state/backup.json').read_text())['backed_up'])
            finally:
                state.close()
