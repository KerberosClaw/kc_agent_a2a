import contextlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]

class HookInstallationTests(unittest.TestCase):
    def test_isolated_install_remove_preserves_unrelated_hooks_and_mail(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp).resolve();runtime=home/'runtime';runtime.mkdir()
            (runtime/'config.json').write_text(json.dumps({'content':str(home/'social'), 'continuity_root':str(home/'continuity'),
                'personas':{a:{'root':str(home/a)} for a in ('agent_a','agent_b')}}))
            claude=home/'.claude/settings.json';claude.parent.mkdir()
            claude.write_text(json.dumps({'hooks':{'Stop':[{'hooks':[{'type':'command','command':'keep-existing'}]}]},'marker':'keep'}))
            codex=home/'.codex/config.toml';codex.parent.mkdir();codex.write_text('model = "keep-existing"\n')
            helper=home/'bin/custom-note'
            def run(script,args):
                with patch('sys.platform','darwin'),patch.object(Path,'home',return_value=home),patch('sys.argv',[script,*args]),contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_path(str(ROOT/'scripts'/script),run_name='__main__')
            run('install_a2a_notes.py',['--runtime',str(runtime),'--helper',str(helper)])
            run('install_a2a_readback.py',['--runtime',str(runtime)])
            saved=(runtime/'note-config.json').read_bytes()
            run('uninstall_a2a_notes.py',['--helper',str(helper)])
            self.assertEqual(saved,(runtime/'note-config.json').read_bytes())
            state=json.loads(claude.read_text());self.assertEqual(state['marker'],'keep')
            commands=[x['command'] for groups in state['hooks'].values() for group in groups for x in group['hooks']]
            self.assertIn('keep-existing',commands)
            self.assertTrue(any('digest-hook.py' in x for x in commands))
            self.assertFalse(any('custom-note' in x for x in commands))
            self.assertEqual(tomllib.loads(codex.read_text())['model'],'keep-existing')
            self.assertNotIn('note mailbox',codex.read_text());self.assertIn('relationship readback',codex.read_text())
