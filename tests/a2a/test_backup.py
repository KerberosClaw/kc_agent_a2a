"""Real encryption + local Git remote, with an ephemeral test key only."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class EncryptedBackupTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('git-crypt') and shutil.which('git'), 'git + git-crypt required')
    def test_encrypt_push_clone_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'source'; source.mkdir()
            env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1', HOME=str(root),
                       GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                       GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
            def run(*args, cwd=source):
                return subprocess.run(args, cwd=cwd, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
            run('git','init','-b','main'); run('git-crypt','init')
            key = root/'ephemeral-test.key'; run('git-crypt','export-key',str(key)); key.chmod(0o600)
            (source/'.gitattributes').write_text('* filter=git-crypt diff=git-crypt\n.gitattributes !filter !diff\n')
            body = b'{"synthetic":true,"messages":["test-only-content"]}\n'
            (source/'snapshot.json').write_bytes(body)
            run('git','add','.'); run('git','commit','-m','test: snapshot')
            encrypted = run('git','show','HEAD:snapshot.json')
            self.assertTrue(encrypted.startswith(b'\x00GITCRYPT\x00'))
            self.assertNotIn(b'test-only-content', encrypted)
            remote = root/'remote.git'; run('git','init','--bare','--initial-branch=main',str(remote))
            run('git','remote','add','origin',str(remote)); run('git','push','origin','main')
            restored = root/'restored'; run('git','clone',str(remote),str(restored))
            self.assertTrue((restored/'snapshot.json').read_bytes().startswith(b'\x00GITCRYPT\x00'))
            run('git-crypt','unlock',str(key),cwd=restored)
            self.assertEqual((restored/'snapshot.json').read_bytes(), body)
            self.assertFalse((restored/'state.sqlite3').exists())
            self.assertFalse((restored/'ephemeral-test.key').exists())

    @unittest.skipUnless(shutil.which('git-crypt') and shutil.which('git'), 'git + git-crypt required')
    def test_content_writer_encrypts_and_verifies_remote(self):
        import uuid
        from unittest.mock import patch
        from a2a.social import save_snapshot
        from a2a.storage import BoundaryError
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir()
            env={'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','HOME':str(root),
                 'GIT_AUTHOR_NAME':'Test','GIT_AUTHOR_EMAIL':'test@example.invalid','GIT_COMMITTER_NAME':'Test','GIT_COMMITTER_EMAIL':'test@example.invalid'}
            with patch.dict(os.environ,env):
                def run(*args):return subprocess.run(args,cwd=source,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
                run('git','init','-b','main');run('git-crypt','init')
                (source/'.gitattributes').write_text('* filter=git-crypt diff=git-crypt\n.gitattributes !filter !diff\n')
                (source/'README.md').write_text('encrypted content test')
                run('git','add','.gitattributes','README.md');run('git','commit','-m','test: init')
                remote=root/'remote.git';run('git','init','--bare','--initial-branch=main',str(remote));run('git','remote','add','origin',str(remote))
                snapshot={'run_id':str(uuid.uuid4()),'messages':[{'author':'test','reply':'private test content'}]}
                result=save_snapshot(source,snapshot)
                self.assertTrue(result['backed_up'])
                self.assertTrue(run('git','show','HEAD:runs/'+snapshot['run_id']+'/snapshot.json').startswith(b'\x00GITCRYPT\x00'))
                (source/'.env').write_text('synthetic credential candidate')
                with self.assertRaises(BoundaryError):save_snapshot(source,{'run_id':str(uuid.uuid4()),'messages':[]})
