import json
import os
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from a2a.native_message import MessageError, NativeMessages, envelope, private_socket


class NativeMessageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.codex=self.root/'codex';self.codex.mkdir()
        db=sqlite3.connect(self.codex/'state_5.sqlite')
        db.execute('CREATE TABLE threads(id,cwd,source,archived)')
        db.execute("INSERT INTO threads VALUES('fictional-thread','/persona/fictional-thread','vscode',0)");db.commit();db.close()
        db=sqlite3.connect(self.codex/'thread_history_1.sqlite')
        db.execute('CREATE TABLE thread_items(thread_id,turn_id,item_type,item_json,rollout_ordinal)');db.close()
        self.config={'receipts':str(self.root/'receipts'),'codex_home':str(self.codex),
                     'claude_home':str(self.root/'claude'),'codex':'/native/codex',
                     'senders':['relay','observer','agent_a','agent_b','agent_c'],
                     'targets':{'agent_b':{'engine':'codex','session':'fictional-thread','cwd':'/persona/fictional-thread'}},
                     'claude_wire_versions':['2.1.280']}
        self.platform=patch('a2a.native_message.sys.platform','darwin');self.platform.start()
        self.service=NativeMessages(self.config)

    def tearDown(self):self.platform.stop();self.tmp.cleanup()

    def record(self,request='req',status='unknown'):
        return {'request_id':request,'sender':'relay','recipient':'agent_b','fingerprint':'test',
                'target':self.config['targets']['agent_b'],'status':status,'reply':None}

    def add(self,turn,kind,value,order):
        db=sqlite3.connect(self.codex/'thread_history_1.sqlite')
        db.execute('INSERT INTO thread_items VALUES(?,?,?,?,?)',('fictional-thread',turn,kind,json.dumps(value),order));db.commit();db.close()

    def test_queue_is_argv_and_preserves_text(self):
        body='literal `id` $(id)\nsecond line'
        with patch('a2a.native_message.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='Queued message abc-123 for thread fictional-thread.',stderr='')) as run:
            # Real session IDs are UUIDs, so use a UUID fixture for the native receipt.
            target=self.config['targets']['agent_b'];target['session']='11111111-1111-4111-8111-111111111111'
            db=sqlite3.connect(self.codex/'state_5.sqlite');db.execute('UPDATE threads SET id=?',(target['session'],));db.commit();db.close()
            run.return_value.stdout=f"Queued message abc-123 for thread {target['session']}."
            result=self.service.send('req','agent_b','relay',body)
            self.assertEqual(result['status'],'queued')
            args=run.call_args.args[0];self.assertEqual(args[:2],['/native/codex','queue'])
            self.assertIn(body,args[-1]);self.assertFalse(run.call_args.kwargs.get('shell',False))
            self.assertIn('不是主人本人',args[-1])

    def test_same_request_never_sends_twice_even_unknown(self):
        with patch('a2a.native_message.subprocess.run',side_effect=TimeoutError('unknown')) as run:
            self.assertEqual(self.service.send('req','agent_b','relay','hello')['status'],'unknown')
            self.assertEqual(self.service.send('req','agent_b','relay','hello')['status'],'unknown')
            self.assertEqual(run.call_count,1)
        with self.assertRaises(MessageError):self.service.send('req','agent_b','relay','different')

    def test_bad_native_receipt_stays_unknown(self):
        with patch('a2a.native_message.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='done',stderr='')):
            self.assertEqual(self.service.send('req','agent_b','relay','hello')['status'],'unknown')

    def test_target_archived_or_changed_cwd_refused_before_send(self):
        db=sqlite3.connect(self.codex/'state_5.sqlite');db.execute('UPDATE threads SET archived=1');db.commit();db.close()
        with patch('a2a.native_message.subprocess.run') as run:
            with self.assertRaises(MessageError):self.service.send('req','agent_b','relay','hello')
            run.assert_not_called()

    def test_invalid_identity_body_and_request(self):
        for sender,recipient,text in [('user','agent_b','x'),('agent_b','agent_b','x'),('relay','other','x'),('relay','agent_b',''),('relay','agent_b','x'*4001)]:
            with self.assertRaises(MessageError):self.service.send('req',recipient,sender,text)
        with self.assertRaises(MessageError):self.service.path('../escape')

    def test_inspect_matches_exact_input_turn_not_quoted_marker(self):
        record=self.record();record['marker']='[A2A 原生轉述 id=req source=relay]';self.service.save(record)
        self.add('wrong','userMessage',{'content':[{'text':'quotation '+record['marker']}]},1)
        self.add('wrong','agentMessage',{'phase':'final_answer','text':'wrong reply'},2)
        self.assertIsNone(self.service.inspect('req')['reply'])
        self.add('right','userMessage',{'content':[{'text':record['marker']+'\nbody'}]},3)
        self.add('right','agentMessage',{'phase':'commentary','text':'working'},4)
        self.add('right','agentMessage',{'phase':'final_answer','text':'right reply'},5)
        result=self.service.inspect('req');self.assertEqual(result['reply'],'right reply')

    def test_ambiguous_codex_inputs_not_claimed_as_reply(self):
        record=self.record();record['marker']='[A2A 原生轉述 id=req source=relay]';self.service.save(record)
        for order,turn in enumerate(['one','two']):self.add(turn,'userMessage',{'content':[{'text':record['marker']+'\nbody'}]},order)
        self.add('one','agentMessage',{'phase':'final_answer','text':'reply'},3)
        self.assertIsNone(self.service.inspect('req')['reply'])



    def test_unknown_claude_wire_version_and_ambiguous_identity_refused(self):
        sessions=Path(self.config['claude_home'])/'sessions';sessions.mkdir(parents=True)
        self.config['targets']['agent_a']={'engine':'claude','cwd':'/persona/agent_a'}
        p=self.root/'peer.sock';s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.bind(str(p))
        value={'sessionId':'agent_a','cwd':'/persona/agent_a','kind':'interactive','pid':os.getpid(),
               'messagingSocketPath':str(p),'version':'unknown'}
        (sessions/'one.json').write_text(json.dumps(value))
        try:
            with self.assertRaisesRegex(MessageError,'unverified'):self.service.target('agent_a')
            value['version']='2.1.280';(sessions/'one.json').write_text(json.dumps(value))
            (sessions/'two.json').write_text(json.dumps(dict(value,sessionId='other')))
            with self.assertRaisesRegex(MessageError,'ambiguous'):self.service.target('agent_a')
        finally:s.close()

    def test_claude_late_inspection_excludes_next_human_reply(self):
        p=self.root/'claude.jsonl'
        rows=[{'type':'user','uuid':'root','origin':{'kind':'peer','msg_id':'transport'},'message':{'content':'peer'}},
              {'type':'assistant','uuid':'final','parentUuid':'root','message':{'stop_reason':'end_turn','content':[{'type':'text','text':'peer reply'}]}},
              {'type':'user','uuid':'human','parentUuid':'final','origin':{'kind':'human'},'message':{'content':'private next question'}},
              {'type':'assistant','uuid':'later','parentUuid':'human','message':{'stop_reason':'end_turn','content':[{'type':'text','text':'private later reply'}]}}]
        p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        record=self.record();record.update(target={'engine':'claude'},transport_id='transport',transcript=str(p),offset=0)
        self.service.save(record);self.assertEqual(self.service.inspect('req')['reply'],'peer reply')

    def test_claude_next_multimodal_human_without_origin_stops_readback(self):
        p=self.root/'claude.jsonl'
        rows=[{'type':'user','uuid':'root','origin':{'kind':'peer','msg_id':'transport'},'message':{'content':'peer'}},
              {'type':'user','uuid':'human','parentUuid':'root','message':{'content':[{'type':'text','text':'private question'},{'type':'image'}]}},
              {'type':'assistant','uuid':'later','parentUuid':'human','message':{'stop_reason':'end_turn','content':[{'type':'text','text':'private reply'}]}}]
        p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        record=self.record();record.update(target={'engine':'claude'},transport_id='transport',transcript=str(p),offset=0)
        self.service.save(record);self.assertIsNone(self.service.inspect('req')['reply'])

    def test_unverified_os_refuses_before_claude_transport(self):
        self.config['targets']['agent_a']={'engine':'claude','cwd':'/persona/agent_a'}
        with patch('a2a.native_message.sys.platform','linux'):
            with self.assertRaisesRegex(MessageError,'OS adapter'):self.service.send('req','agent_a','relay','hello')
        self.assertFalse(self.service.path('req').exists())

    def test_private_socket_rejects_symlink_and_writable_directory(self):
        p=self.root/'test.sock';s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.bind(str(p))
        try:
            self.assertEqual(private_socket(p),p)
            link=self.root/'link';link.symlink_to(p)
            with self.assertRaises(MessageError):private_socket(link)
            os.chmod(self.root,0o777)
            with self.assertRaises(MessageError):private_socket(p)
        finally:s.close();os.chmod(self.root,0o700)

    def test_envelope_reply_address_is_explicit(self):
        value=envelope('req','relay','body','uds:/tmp/cc-socks/100.sock')
        self.assertIn('uds:/tmp/cc-socks/100.sock',value);self.assertIn('不猜其他session',value)


if __name__=='__main__':unittest.main()
