import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/migrate_native_messages.py'
spec=importlib.util.spec_from_file_location('note_retirement',SCRIPT)
retirement=importlib.util.module_from_spec(spec);spec.loader.exec_module(retirement)


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
        self.runtime=self.home/'Library/Application Support/kc-agent-a2a'
        self.runtime.mkdir(parents=True)
        self.helper=self.home/'.local/bin/agent-note';self.helper.parent.mkdir(parents=True);self.helper.write_text('from a2a.note_runtime import main')
        self.config=self.runtime/'note-config.json'
        self.mail=self.runtime/'mailbox';(self.mail/'.state').mkdir(parents=True)
        (self.mail/'.state/coordinator.lock').touch();(self.mail/'.state/state.sqlite3').write_bytes(b'unchanged ledger bytes')
        (self.mail/'claimed.json').write_bytes(b'original pending note bytes')
        self.config.write_text(json.dumps({'mail_root':str(self.mail),'helper':str(self.helper)}))
        self.claude=self.home/'.claude/settings.json';self.claude.parent.mkdir()
        command=str(self.helper)+' hook'
        self.claude.write_text(json.dumps({'permissions':{'allow':['Read']},'hooks':{
            'SessionStart':[{'hooks':[{'command':'night-digest','type':'command'}]}],
            'UserPromptSubmit':[{'hooks':[{'command':command,'type':'command'},{'command':'other-hook','type':'command'}]}]}}))
        self.codex=self.home/'.codex/config.toml';self.codex.parent.mkdir()
        self.codex.write_text('model="preserved"\n[hooks]\ntrusted_commands=["preserved-trust"]\n'+retirement.START+'\n[[hooks.UserPromptSubmit]]\n[[hooks.UserPromptSubmit.hooks]]\ntype="command"\ncommand='+json.dumps(command)+'\n'+retirement.END+'\n[[hooks.SessionStart]]\n[[hooks.SessionStart.hooks]]\ntype="command"\ncommand="night-digest"\n')
        self.native=self.home/'.local/bin/agent-message';self.native.write_text('native helper remains unchanged')
        self.before={p:p.read_bytes() for p in [self.helper,self.config,self.claude,self.codex,self.native]}
        self.archive=self.runtime/'retired-notes/test'
        self.active=patch.object(retirement,'active_helpers',return_value=[]);self.active.start()

    def tearDown(self):self.active.stop();self.tmp.cleanup()

    def test_dry_run_makes_no_changes(self):
        result=retirement.retire(self.home,self.archive)
        self.assertEqual(result['hooks_to_remove'],2)
        self.assertFalse(self.archive.exists());self.assertTrue(self.mail.exists())
        for p,data in self.before.items():self.assertEqual(p.read_bytes(),data)

    def test_retirement_preserves_data_and_other_hooks_and_disables_old_calls(self):
        result=retirement.retire(self.home,self.archive,apply=True)
        self.assertEqual(result['status'],'retired')
        self.assertFalse(self.mail.exists());self.assertFalse(self.config.exists())
        self.assertEqual((self.archive/'agent_mail/claimed.json').read_bytes(),b'original pending note bytes')
        self.assertEqual((self.archive/'agent_mail/.state/state.sqlite3').read_bytes(),b'unchanged ledger bytes')
        self.assertEqual(self.native.read_bytes(),self.before[self.native])
        self.assertEqual(json.loads(self.claude.read_text())['permissions'],{'allow':['Read']})
        self.assertEqual(json.loads(self.claude.read_text())['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'],'other-hook')
        value=tomllib.loads(self.codex.read_text());self.assertEqual(value['model'],'preserved')
        self.assertEqual(value['hooks']['trusted_commands'],['preserved-trust'])
        self.assertNotIn('UserPromptSubmit',value['hooks']);self.assertEqual(value['hooks']['SessionStart'][0]['hooks'][0]['command'],'night-digest')
        for args,code in [(['hook'],0),(['send','fake','alice'],2),(['relay','receive'],2)]:
            process=subprocess.run([sys.executable,str(self.helper),*args],capture_output=True,text=True)
            self.assertEqual(process.returncode,code);self.assertEqual(process.stdout,'')
        self.assertFalse(self.mail.exists())

    def test_busy_mailbox_aborts_before_mutation(self):
        with (self.mail/'.state/coordinator.lock').open('r+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):retirement.retire(self.home,self.archive,apply=True)
        self.assertFalse(self.archive.exists())
        for p,data in self.before.items():self.assertEqual(p.read_bytes(),data)

    def test_unrelated_setting_in_managed_block_is_preserved_by_refusing(self):
        self.codex.write_text(self.codex.read_text().replace(retirement.START,retirement.START+'\n[unrelated]\nkeep=true\n'))
        with self.assertRaises(ValueError):retirement.retire(self.home,self.archive,apply=True)
        self.assertFalse(self.archive.exists());self.assertTrue(self.mail.exists())

    def test_symlinked_mail_or_wrong_archive_is_rejected(self):
        with self.assertRaises(ValueError):retirement.retire(self.home,self.home/'public',apply=True)
        (self.mail/'link').symlink_to(self.native)
        with self.assertRaises(ValueError):retirement.retire(self.home,self.archive,apply=True)
        self.assertFalse(self.archive.exists())

    def test_failed_settings_write_restores_original_state(self):
        original=retirement.atomic_write;failed=False
        def injected(path,data):
            nonlocal failed
            if path==self.codex and not failed:failed=True;raise OSError('injected write failure')
            return original(path,data)
        with patch.object(retirement,'atomic_write',side_effect=injected):
            with self.assertRaises(OSError):retirement.retire(self.home,self.archive,apply=True)
        self.assertTrue(self.mail.exists())
        for p,data in self.before.items():self.assertEqual(p.read_bytes(),data)


if __name__=='__main__':unittest.main()
