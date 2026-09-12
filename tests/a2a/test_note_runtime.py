import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from a2a.note_runtime import Service
from a2a.storage import BoundaryError


class NoteRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sessions = {a: str(uuid.uuid4()) for a in ('agent_a', 'agent_b')}
        self.config = {'mail_root': str(self.root/'mail'), 'helper': '/test/agent-note',
                       'transcript_roots': [str(self.root)], 'sessions': {
                         sid: {'agent': a, 'cwd': str(self.root), 'engine': 'claude' if a == 'agent_a' else 'codex'}
                         for a, sid in self.sessions.items()}}
        self.service = Service(self.config)
        self.events = {}

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def append(self, agent, data):
        with (self.root/(agent+'.jsonl')).open('a') as f:
            f.write(json.dumps(data)+'\n')

    def begin(self, agent, text, *, meta=False, channel=False):
        turn = str(uuid.uuid4()); sid = self.sessions[agent]
        event = {'session_id': sid, 'cwd': str(self.root), 'hook_event_name': 'UserPromptSubmit',
                 'transcript_path': str(self.root/(agent+'.jsonl')), 'prompt': text,
                 ('prompt_id' if agent == 'agent_a' else 'turn_id'): turn}
        self.service.hook(event)  # Native prompt may not be flushed until after this callback.
        if agent == 'agent_a':
            data = {'type': 'user', 'sessionId': sid, 'promptId': turn, 'uuid': str(uuid.uuid4()),
                    'isMeta': meta, 'origin': {'kind': 'human'}, 'message': {'role': 'user', 'content': text}}
            if channel:
                data['origin'] = {'kind': 'channel', 'server': 'plugin:telegram:telegram'}
            self.append(agent, data)
        else:
            self.append(agent, {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': turn}})
            self.append(agent, {'type': 'response_item', 'payload': {'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}})
        self.events[agent] = event
        row = self.service.store.db.execute('SELECT token FROM mail_capabilities WHERE session=? AND input_id=?', (sid, turn)).fetchone()
        return row[0] if row else None

    def finish(self, agent):
        e = self.events[agent]
        self.service.hook(dict(e, hook_event_name='Stop', last_assistant_message='received'))
        # Only the subsequent native terminal event is completion evidence.
        if agent == 'agent_a':
            self.append(agent, {'type': 'assistant', 'message': {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'received'}]}})
            self.append(agent, {'type': 'system', 'subtype': 'turn_duration'})
        else:
            self.append(agent, {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': e['turn_id'], 'last_agent_message': 'received'}})

    def send(self):
        token = self.begin('agent_a', '幫我跟Agent B說，明天再看。')
        result = self.service.command('send', token, recipient='agent_b', body='明天再看。')
        self.finish('agent_a')
        return result['note_id']

    def test_supplied_body_preserves_exact_original(self):
        body = '\n  exactly\nunchanged  \n'
        token = self.begin('agent_a', '把這段原文傳給Agent B：'+body)
        self.service.command('send', token, recipient='agent_b', body=body)
        packet = self.service.mail.claim('agent_b', 'receiver', 'turn')[0]
        self.assertEqual(packet['body'], body)

    def test_verbatim_body_is_selected_from_native_input(self):
        body = '\n  $(not shell)\nexactly  \n'
        token = self.begin('agent_a', '請把這段原封不動轉給Agent B：'+body)
        self.service.command('send', token, recipient='agent_b', verbatim_after='Agent B：')
        self.assertEqual(self.service.mail.claim('agent_b', 'receiver', 'turn')[0]['body'], body)
        token = self.begin('agent_a', '重複：標記：本文')
        for marker in [':', '：', '', 3]:
            with self.assertRaises(BoundaryError):
                self.service.command('send', token, recipient='agent_b', verbatim_after=marker)

    def test_free_form_turn_offers_composition(self):
        token = self.begin('agent_a', '把今天討論的內容給Agent B吧')
        self.assertIsNotNone(token, 'Explicit relay request must not require a fixed sentence pattern')
        self.service.command('send', token, recipient='agent_b', body='今天決定把演練移到週五。')
        packet = self.service.mail.claim('agent_b', 'receiver', 'turn')[0]
        self.assertEqual(packet['body'], '今天決定把演練移到週五。')
        self.assertEqual(packet['author'], 'user')
        self.assertEqual(packet['origin_input_id'], self.events['agent_a']['prompt_id'])

    def test_capability_offer_does_not_send_or_mark_content_turn(self):
        for text in ['早安', '假如我要你轉告Agent B呢？', '先擬給我看', '不要寄', '他說「轉告Agent B」']:
            token = self.begin('agent_a', text)
            self.assertIsNotNone(token)
        self.assertEqual(self.service.store.db.execute('SELECT count(*) FROM notes').fetchone()[0], 0)
        self.assertEqual(self.service.store.db.execute('SELECT count(*) FROM mail_turns').fetchone()[0], 0)
        self.service.command('query', token)
        self.service.command('receive', token)
        self.assertEqual(self.service.store.db.execute('SELECT count(*) FROM mail_turns').fetchone()[0], 0)

    def test_stale_or_unflushed_capability_cannot_send(self):
        token = self.begin('agent_a', '把剛才的事告訴Agent B')
        self.begin('agent_a', '等等，先不要寄')
        with self.assertRaises(BoundaryError):
            self.service.command('send', token, recipient='agent_b', body='old')
        event = dict(self.events['agent_a'], prompt_id=str(uuid.uuid4()))
        self.service.hook(event)
        token = self.service.store.db.execute('SELECT token FROM mail_capabilities WHERE input_id=?', (event['prompt_id'],)).fetchone()[0]
        with self.assertRaises(BoundaryError):
            self.service.command('send', token, recipient='agent_b', body='not flushed')

    def test_legacy_capability_and_receipt_survive_upgrade(self):
        token = self.begin('agent_a', '請幫忙轉達')
        row = self.service.store.db.execute('SELECT * FROM mail_capabilities WHERE token=?', (token,)).fetchone()
        self.service.track(row)
        self.service.store.db.execute('DELETE FROM mail_capabilities')
        self.service.close(); self.service = Service(self.config)
        self.assertEqual(self.service.command('act', token)['status'], 'action_required')
        result = self.service.command('send', token, recipient='agent_b', body='legacy')
        self.service.store.db.execute('DELETE FROM mail_actions')
        self.service.store.db.execute('INSERT INTO mail_operations VALUES(?,?)', (token, json.dumps(result)))
        self.finish('agent_a')
        self.assertEqual(self.service.command('act', token)['note_id'], result['note_id'])
        self.assertEqual(self.service.command('send', token, recipient='agent_b', body='retry')['note_id'], result['note_id'])

    def test_cli_json_body_never_becomes_shell_code_or_source_id(self):
        token = self.begin('agent_a', '原文轉交給Agent B')
        config = self.root/'config.json'; config.write_text(json.dumps(self.config))
        body = '  $(touch forbidden) `echo secret`\n"quote" \\ newline\n'
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]/'src'))
        command = [sys.executable, '-B', '-m', 'a2a.note_runtime', '--config', str(config), 'send', token, 'agent_b']
        run = subprocess.run(command, input=json.dumps({'body': body}), text=True, capture_output=True, env=env, cwd=self.root)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.service.mail.claim('agent_b', 'receiver', 'turn')[0]['body'], body)
        self.assertFalse((self.root/'forbidden').exists())
        bad = subprocess.run(command, input=json.dumps({'body': body, 'source_input': 'fake'}), text=True, capture_output=True, env=env, cwd=self.root)
        self.assertNotEqual(bad.returncode, 0)
        self.assertNotIn(body, bad.stdout)

    def test_helper_enforces_body_and_recipient_boundaries(self):
        token = self.begin('agent_a', '請轉交')
        for recipient, body in [('agent_a', 'x'), ('../unknown', 'x'), ('agent_b', ''), ('agent_b', 'x'*4001), ('agent_b', {})]:
            with self.subTest(recipient=recipient), self.assertRaises(BoundaryError):
                self.service.command('send', token, recipient=recipient, body=body)
        self.assertEqual(self.service.store.db.execute('SELECT count(*) FROM mail_turns').fetchone()[0], 0)

    def test_send_uses_native_input_and_deduplicates(self):
        token = self.begin('agent_a', '幫我跟Agent B說，明天再看。')
        first = self.service.command('send', token, recipient='agent_b', body='明天再看。')
        self.assertEqual(first['note_id'], self.service.command('send', token, recipient='agent_b', body='changed retry')['note_id'])
        self.assertEqual(self.service.store.db.execute('SELECT count(*) FROM notes').fetchone()[0], 1)
        self.finish('agent_a')
        self.assertEqual(first['note_id'], self.service.command('send', token, recipient='agent_b', body='retry')['note_id'])
        self.assertEqual(self.service.store.db.execute('SELECT count(*) FROM notes').fetchone()[0], 1)

    def test_both_directions_and_native_terminal_receipt(self):
        note = self.send(); token = self.begin('agent_b', '早安')
        result = self.service.command('receive', token)
        self.assertEqual(result['notes'][0]['author'], 'user')
        self.assertEqual(result['notes'][0]['courier'], 'agent_a')
        self.service.command('ack', token, [note])
        self.service.reconcile(); self.assertEqual(self.service.mail.status(note), 'delivered')
        self.finish('agent_b'); self.service.reconcile()
        self.assertEqual(self.service.mail.status(note), 'burned')
        token = self.begin('agent_b', '幫我轉告Agent A熊，收到。')
        other = self.service.command('send', token, recipient='agent_a', body='收到。')['note_id']; self.finish('agent_b')
        token = self.begin('agent_a', '<channel source="plugin:telegram:telegram" message_id="test">\n早安\n</channel>', meta=True, channel=True)
        self.service.command('receive', token); self.service.command('ack', token, [other])
        self.finish('agent_a'); self.service.reconcile()
        self.assertEqual(self.service.mail.status(other), 'burned')
        self.assertFalse(list((self.root/'mail/agent_a').rglob('*.json')))

    def test_other_turn_or_text_alone_cannot_ack(self):
        note = self.send(); token = self.begin('agent_b', '早安')
        self.service.command('receive', token)
        self.finish('agent_b'); self.service.reconcile()
        self.assertEqual(self.service.mail.status(note), 'delivered')
        self.begin('agent_b', '我看到了 '+note)
        with self.assertRaises(BoundaryError): self.service.command('ack', token, [note])
        self.finish('agent_b'); self.service.reconcile()
        self.assertEqual(self.service.mail.status(note), 'delivered')

    def test_stop_without_terminal_does_not_burn(self):
        note = self.send(); token = self.begin('agent_b', 'hi')
        self.service.command('receive', token); self.service.command('ack', token, [note])
        self.service.hook(dict(self.events['agent_b'], hook_event_name='Stop', last_assistant_message='done'))
        self.service.reconcile(); self.assertEqual(self.service.mail.status(note), 'delivered')

    def test_failure_releases_same_note_for_new_human_turn(self):
        note = self.send(); token = self.begin('agent_b', 'hi')
        self.service.command('receive', token)
        self.service.hook(dict(self.events['agent_b'], hook_event_name='Interrupt'))
        self.assertEqual(self.service.mail.status(note), 'new')
        token = self.begin('agent_b', '再試')
        self.assertEqual(self.service.command('receive', token)['notes'][0]['id'], note)

    def test_system_input_and_unregistered_session_do_not_send(self):
        token = self.begin('agent_a', '幫我跟Agent B說，hi', meta=True)
        with self.assertRaises(BoundaryError): self.service.command('send', token, recipient='agent_b', body='hi')
        event = dict(self.events['agent_a'], session_id=str(uuid.uuid4()))
        self.assertIsNone(self.service.hook(event))
        self.assertEqual(self.service.store.db.execute('SELECT count(*) FROM notes').fetchone()[0], 0)

    def test_cancel_before_read_and_refuse_after_injection(self):
        note = self.send(); token = self.begin('agent_a', '取消剛才給Agent B那張，還沒送達的話就不要送。')
        self.assertTrue(self.service.command('cancel', token, recipient='agent_b')['cancelled']); self.finish('agent_a')
        self.assertEqual(self.service.mail.status(note), 'cancelled')
        note = self.send(); token = self.begin('agent_b', 'hi'); self.service.command('receive', token)
        token = self.begin('agent_a', '取消剛才給Agent B那張')
        self.assertFalse(self.service.command('cancel', token, recipient='agent_b')['cancelled'])

    def test_query_and_cancel_are_scoped_and_can_select_a_note(self):
        first = self.send(); second = self.send()
        self.service.mail.send(courier='agent_b', recipient='agent_a', source_session='other', source_input='other', body='other')
        token = self.begin('agent_a', '撤回第二張')
        self.assertEqual(len(self.service.command('query', token)['notes']), 2)
        self.assertEqual(self.service.command('cancel', token, recipient='agent_b')['status'], 'needs_clarification')
        with self.assertRaises(BoundaryError):
            self.service.command('cancel', token, recipient='agent_b', note_id='unknown')
        self.assertTrue(self.service.command('cancel', token, recipient='agent_b', note_id=second)['cancelled'])
        self.assertEqual(self.service.mail.status(first), 'new')

    def test_stop_continuation_keeps_capability_until_native_completion(self):
        token = self.begin('agent_a', '把結論給Agent B')
        self.service.hook(dict(self.events['agent_a'], hook_event_name='Stop', last_assistant_message='continuing'))
        self.service.command('send', token, recipient='agent_b', body='conclusion')
        self.finish('agent_a')
        self.begin('agent_a', '剛才那張再寄一次')
        new_token = self.service.store.db.execute('SELECT token FROM mail_capabilities WHERE input_id=?', (self.events['agent_a']['prompt_id'],)).fetchone()[0]
        self.service.command('send', new_token, recipient='agent_b', body='conclusion')
        self.assertEqual(self.service.store.db.execute('SELECT count(*) FROM notes').fetchone()[0], 2)

    def test_receive_once_per_native_turn(self):
        for i in range(7):
            self.service.mail.send(courier='agent_a', recipient='agent_b', source_session='s', source_input=str(i), body='body')
        token = self.begin('agent_b', 'hi')
        self.assertEqual(len(self.service.command('receive', token)['notes']), 5)
        self.assertEqual(self.service.command('receive', token)['status'], 'already_claimed_this_turn')
        self.assertEqual(self.service.store.db.execute("SELECT count(*) FROM notes WHERE status='new'").fetchone()[0], 2)

    def test_receipt_time_commits_before_unlink(self):
        note = self.send(); token = self.begin('agent_b', 'hi')
        self.service.command('receive', token); self.service.command('ack', token, [note]); self.finish('agent_b')
        with patch.object(Path, 'unlink', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): self.service.reconcile()
        self.assertEqual(self.service.mail.status(note), 'acknowledged')
        self.assertEqual(self.service.store.db.execute("SELECT count(*) FROM mail_events WHERE note=? AND event='received'", (note,)).fetchone()[0], 1)
        self.service.reconcile()
        self.assertEqual(self.service.mail.status(note), 'burned')
        self.assertEqual(self.service.store.db.execute("SELECT count(*) FROM mail_events WHERE note=? AND event='received'", (note,)).fetchone()[0], 1)


if __name__ == '__main__': unittest.main()
