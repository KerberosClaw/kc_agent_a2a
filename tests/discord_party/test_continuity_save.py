import concurrent.futures
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from discord_party.continuity import Continuity
from discord_party.continuity_save import begin, commit_save, reconcile
from discord_party.state import NotReady
from test_continuity import message, summary


def repository(root):
    root.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True).stdout
    git('init', '-b', 'main'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid')
    subprocess.run(['git-crypt', 'init'], cwd=root, check=True, capture_output=True)
    (root / '.gitattributes').write_text('* filter=git-crypt diff=git-crypt\n.gitattributes !filter !diff\n')
    (root / 'agent_a_testament.md').write_text('Synthetic canonical personality')
    (root / 'patches').mkdir(); (root / 'journal').mkdir()
    git('add', '.gitattributes', 'agent_a_testament.md'); git('commit', '-m', 'test: init')
    return git


@unittest.skipUnless(shutil.which('git-crypt'), 'git-crypt required')
class SaveCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.repo = self.root / 'persona'; self.git = repository(self.repo)
        self.s = Continuity(self.root / 'state')
        self.s.ingest([message(1)], now=0)
        job = self.s.reserve('agent_a', 'v1', force=True, now=1)
        self.s.publish(job, summary(job), now=2)
        self.s.claim('agent_a', 'main', 'turn'); self.s.acknowledge('agent_a', 'main', 'turn')

    def tearDown(self):
        self.s.close(); self.tmp.cleanup()

    def write_journal(self):
        (self.repo / 'journal/20260912.md').write_text('2026-09-12｜主線：合成｜人：測試｜新梗：無\nA synthetic observation.\n')

    def save(self, sid):
        return commit_save(self.s, self.repo, 'agent_a', sid, ['journal/20260912.md'], 'docs: journal 20260912')

    def test_save_is_separate_frozen_and_no_drift_patch_needed(self):
        pending = begin(self.s, self.repo, 'agent_a')
        self.assertEqual(len(pending['documents']), 1)
        self.assertEqual(len(self.s.pending_save('agent_a')), 1)
        self.s.ingest([message(2)], now=10)
        job = self.s.reserve('agent_a', 'v1', force=True, now=11); self.s.publish(job, summary(job), now=12)
        self.s.claim('agent_a', 'main', 'next'); self.s.acknowledge('agent_a', 'main', 'next')
        self.write_journal(); (self.repo / 'unrelated.txt').write_text('preserve me')
        result = self.save(pending['save_id'])
        self.assertFalse(result['backed_up'])  # no remote is a backup failure, not data loss
        self.assertTrue(result['checkpoint_may_clear'])
        self.assertEqual(len(self.s.pending_save('agent_a')), 1)
        self.assertEqual(list((self.repo / 'patches').glob('*')), [])
        self.assertTrue((self.repo / 'unrelated.txt').exists())
        self.assertIn(b'?? unrelated.txt', self.git('status', '--short'))

    def test_commit_then_crash_reconciles_without_second_commit(self):
        pending = begin(self.s, self.repo, 'agent_a'); self.write_journal()
        with patch('discord_party.continuity_save.mark_committed', side_effect=RuntimeError('simulated crash')):
            with self.assertRaises(RuntimeError):
                self.save(pending['save_id'])
        head = self.git('rev-parse', 'HEAD')
        self.s.close(); self.s = Continuity(self.root / 'state')
        result = self.save(pending['save_id'])
        self.assertTrue(result['already_committed'])
        self.assertEqual(head, self.git('rev-parse', 'HEAD'))
        self.assertEqual(self.s.pending_save('agent_a'), [])

    def test_duplicate_save_and_changed_head_blocked(self):
        pending = begin(self.s, self.repo, 'agent_a')
        with self.assertRaises(NotReady):
            begin(self.s, self.repo, 'agent_a')
        self.git('commit', '--allow-empty', '-m', 'test: concurrent commit')
        self.write_journal()
        with self.assertRaises(NotReady):
            self.save(pending['save_id'])
        self.assertEqual(len(self.s.pending_save('agent_a')), 1)

    def test_path_escape_and_unrelated_staging_never_commit(self):
        pending = begin(self.s, self.repo, 'agent_a'); self.write_journal()
        with self.assertRaises(NotReady):
            commit_save(self.s, self.repo, 'agent_a', pending['save_id'], ['../secret.md'], 'docs: journal 20260912')
        (self.repo / 'unrelated.txt').write_text('private unrelated')
        self.git('add', 'unrelated.txt')
        with self.assertRaises(NotReady):
            self.save(pending['save_id'])
        self.assertEqual(len(self.s.pending_save('agent_a')), 1)

    def test_uncommitted_concurrent_patch_change_blocks_save(self):
        pending = begin(self.s, self.repo, 'agent_a'); self.write_journal()
        (self.repo / 'patches/other.md').write_text('A different writer changed this')
        with self.assertRaises(NotReady):
            self.save(pending['save_id'])

    def test_current_turn_claim_can_be_saved_before_terminal_reply(self):
        from discord_party.continuity_hook import prepare
        prepare(self.s)
        self.s.db.execute('DELETE FROM received')
        claim = self.s.claim('agent_a', 'native-session', 'native-turn')
        transcript = self.root / 'native.jsonl'
        transcript.write_text(json.dumps({'type': 'user', 'sessionId': 'native-session', 'promptId': 'native-turn',
                                         'message': {'content': 'save'}}) + '\n')
        self.s.db.execute('INSERT INTO native_claims VALUES(?,?,?)', (claim['claim_id'], 'claude', str(transcript)))
        pending = begin(self.s, self.repo, 'agent_a', claim_id=claim['claim_id'], config={'transcript_roots': [str(self.root)]})
        self.assertEqual(len(pending['documents']), 1)
        self.write_journal(); self.save(pending['save_id'])
        self.assertEqual(self.s.unread('agent_a'), [])
        self.assertEqual(self.s.pending_save('agent_a'), [])


if __name__ == '__main__':
    unittest.main()
