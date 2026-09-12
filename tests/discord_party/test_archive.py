import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_state import registry, event
from test_native import packet
from discord_party.archive import save_room, repository_lock
from discord_party.native import write_private
from discord_party.state import State


@unittest.skipUnless(shutil.which('git-crypt'), 'git-crypt required')
class ArchiveCase(unittest.TestCase):
    def test_real_encrypted_backup_dedup_memories_and_shared_writer_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);runtime = root / 'runtime';runtime.mkdir()
            grant = runtime / 'grant';packet(grant)
            content = root / 'content';content.mkdir()
            writer_lock = root / 'existing.lock';writer_lock.touch()
            def git(*args):
                return subprocess.run(['git', '-C', str(content), *args], check=True, capture_output=True).stdout
            git('init', '-b', 'main')
            git('config', 'user.name', 'Test');git('config', 'user.email', 'test@example.invalid')
            subprocess.run(['git-crypt', 'init'], cwd=content, check=True, capture_output=True)
            (content / '.gitattributes').write_text('* filter=git-crypt diff=git-crypt\n.gitattributes !filter !diff\n')
            git('add', '.gitattributes');git('commit', '-m', 'test: init')
            subprocess.run(['git', 'init', '--bare', str(root / 'remote.git')], check=True, capture_output=True)
            git('remote', 'add', 'origin', str(root / 'remote.git'))
            for agent, bot in [('agent_a', '20'), ('agent_b', '30')]:
                reg = registry(bot)
                write_private(runtime / 'config' / (agent + '.json'), reg.canonical())
                state = State(runtime / 'state' / agent, reg, initialize=True)
                state.synchronized()
                for msg in [event(100), event(101, '20', 'public reply'), event(102, '30', 'peer reply')]:
                    state.ingest(msg)
                state.close()
            result = save_room(runtime, content, grant, writer_lock)
            self.assertEqual(result['message_count'], 3)
            self.assertTrue(result['backed_up'])
            path = 'discord/1/2/messages.jsonl'
            self.assertTrue(git('show', 'HEAD:' + path).startswith(b'\x00GITCRYPT\x00'))
            self.assertEqual(len((content / path).read_text().splitlines()), 3)
            first = result['commit']
            self.assertEqual(save_room(runtime, content, grant, writer_lock)['commit'], first)
            memories = [json.loads((runtime / 'memory' / (a + '.json')).read_text()) for a in ['agent_a', 'agent_b']]
            self.assertEqual([m['self_id'] for m in memories], ['20', '30'])
            self.assertEqual([m['message_id'] for m in memories[0]['messages']], ['100', '101', '102'])
            # Force a new export while the existing night-chat writer holds its lock.
            (runtime / 'evidence/archive.json').unlink()
            with repository_lock(content, writer_lock):
                with self.assertRaises(BlockingIOError):
                    save_room(runtime, content, grant, writer_lock)
            (content / 'unrelated.txt').write_text('do not stage me')
            with self.assertRaises(Exception):
                save_room(runtime, content, grant, writer_lock)
            self.assertEqual(git('diff', '--cached', '--name-only'), b'')
