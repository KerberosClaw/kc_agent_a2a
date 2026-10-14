import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from a2a.native_message import NativeMessages, MessageError, main, validate_config, load_config

script=Path(__file__).resolve().parents[2]/'scripts/install_native_messages.py'
spec=importlib.util.spec_from_file_location('native_install',script)
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


def config(root):
    return {'receipts':str(root/'receipts'),'codex_home':str(root/'codex'),
            'claude_home':str(root/'claude'),'codex':'/fictional/bin/codex',
            'senders':['alice','relay'],'targets':{'alice':{'engine':'claude','cwd':'/fictional/alice'}},
            'claude_wire_versions':['2.1.280']}


class SetupTests(unittest.TestCase):
    def test_install_only_helper_preserves_settings_and_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'config.json';p.write_text(json.dumps(config(root)));p.chmod(0o600)
            settings=root/'settings';settings.write_text('unchanged')
            helper=root/'bin/agent-message';before=p.read_bytes()
            self.assertFalse(installer.install(p,helper)['hooks_modified'])
            self.assertEqual(p.read_bytes(),before);self.assertEqual(settings.read_text(),'unchanged')
            self.assertEqual(helper.stat().st_mode&0o777,0o700)
            self.assertIn('from a2a.native_message import main',helper.read_text())
            old=helper.read_bytes();installer.install(p,helper)
            self.assertEqual(helper.with_name('agent-message.before-native-update').read_bytes(),old)
            with self.assertRaises(ValueError):installer.install(p,helper)

    def test_bad_config_and_occupied_helper_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);value=config(root)
            value['targets']['alice']['engine']='unknown'
            with self.assertRaises(MessageError):validate_config(value)
            value=config(root);value['senders']=['bad\nlabel']
            with self.assertRaises(MessageError):validate_config(value)
            value=config(root);value['claude_wire_versions']=['future']
            with self.assertRaises(MessageError):validate_config(value)
            p=root/'config.json';p.write_text(json.dumps(config(root)));p.chmod(0o600)
            helper=root/'occupied';helper.write_text('other program')
            with self.assertRaises(ValueError):installer.install(p,helper)
            self.assertEqual(helper.read_text(),'other program')

    def test_auto_id_and_explicit_retry_id_are_internal_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'config.json';p.write_text(json.dumps(config(root)))
            ids=[]
            def send(self,request,*args):ids.append(request);return {'request_id':request,'status':'queued'}
            for flags in [[],[],['--request-id','existing']]:
                argv=['agent-message','--config',str(p),'send','alice','--sender','relay',*flags]
                with patch('sys.argv',argv),patch('sys.stdin',io.StringIO('{"text":"hello"}')),patch.object(NativeMessages,'send',send),contextlib.redirect_stdout(io.StringIO()):main()
            self.assertNotEqual(ids[0],ids[1]);self.assertEqual(ids[2],'existing')

    def test_duplicate_config_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.json';p.write_text('{"targets":{"alice":{},"alice":{}}}')
            with self.assertRaisesRegex(MessageError,'duplicate'):load_config(p)

    def test_public_targets_are_not_fixed_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            value=config(Path(tmp))
            for name in ['bob','charlie','dana']:value['targets'][name]={'engine':'claude','cwd':'/fictional/'+name}
            self.assertEqual(len(NativeMessages(value).config['targets']),4)

    def test_receipt_symlink_and_world_readable_root_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);service=NativeMessages(config(root));service.path('id').symlink_to(root/'elsewhere')
            with self.assertRaises(MessageError):service.path('id')
            service.root.chmod(0o755)
            with self.assertRaises(MessageError):NativeMessages(config(root))
