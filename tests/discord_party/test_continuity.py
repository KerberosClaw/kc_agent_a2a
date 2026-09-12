import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from discord_party.continuity import Continuity, decode_result
from discord_party.continuity_hook import handle, reconcile
from discord_party.native import NativeEngine
from discord_party.state import NotReady


def message(n, content=None):
    return {'channel_id': '2', 'message_id': str(n), 'author_id': '10',
            'created_at': '2026-09-12T00:00:00+00:00', 'content': content or 'Human said ' + str(n)}


def summary(job):
    return {'overview': 'Synthetic attributed conversation', 'observations': [
        {'kind': 'said', 'text': 'Author 10 said something; this is a report, not a verified fact.',
         'refs': [str(job['refs'][0])]}]}


class ContinuityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.s = Continuity(self.root / 'state')

    def tearDown(self):
        self.s.close(); self.tmp.cleanup()

    def segment(self, n, agent='agent_a'):
        self.s.ingest([message(n)], now=n)
        job = self.s.reserve(agent, 'v1', force=True, now=n)
        self.assertTrue(self.s.publish(job, summary(job), now=n + 1))
        return job['id']

    def test_duplicate_late_and_revision_are_distinct(self):
        self.s.ingest([message(200)], now=100)
        self.assertEqual(self.s.ingest([message(200)], now=200), 0)
        self.segment(201)
        self.assertEqual(self.s.ingest([message(100)], now=202), 1)
        self.assertEqual(self.s.ingest([message(200, 'corrected')], now=203), 1)
        self.assertEqual(self.s.ingest([dict(message(200), deleted=True)], now=204), 1)
        job = self.s.reserve('agent_a', 'v1', force=True, now=205)
        self.assertEqual(len(job['refs']), 3)
        self.assertTrue(job['inputs'][-1]['body']['deleted'])

    def test_idle_and_continuous_cutoff_and_capacity(self):
        self.s.ingest([message(1)], now=0)
        self.assertIsNone(self.s.reserve('agent_a', 'v1', now=1199))
        self.s.ingest([message(2)], now=7100)
        job = self.s.reserve('agent_a', 'v1', now=7200)
        self.assertEqual(len(job['refs']), 2)
        self.s.ingest([message(3)], now=7300)
        self.assertTrue(self.s.publish(job, summary(job), now=7201))
        next_job = self.s.reserve('agent_a', 'v1', now=7300, max_messages=1)
        self.assertEqual([s['body']['message_id'] for s in next_job['inputs']], ['3'])

    def test_two_connections_cannot_reserve_same_range(self):
        self.s.ingest([message(1)], now=0)
        barrier = threading.Barrier(2)
        def reserve():
            state = Continuity(self.root / 'state')
            try:
                barrier.wait()
                return state.reserve('agent_a', 'v1', force=True, now=1)
            finally:
                state.close()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            jobs = list(pool.map(lambda _: reserve(), range(2)))
        self.assertEqual(sum(j is not None for j in jobs), 1)

    def test_crash_lease_fences_old_worker(self):
        self.s.ingest([message(1)], now=0)
        old = self.s.reserve('agent_a', 'v1', now=1, force=True)
        new = self.s.reserve('agent_a', 'v1', now=602, force=True)
        self.assertEqual(old['id'], new['id'])
        self.assertNotEqual(old['token'], new['token'])
        with self.assertRaises(NotReady):
            self.s.publish(old, summary(old), now=603)
        self.assertTrue(self.s.publish(new, summary(new), now=603))
        with self.assertRaises(NotReady):
            self.s.publish(new, summary(new), now=604)

    def test_invalid_refs_never_publish_or_advance(self):
        self.s.ingest([message(1)], now=0)
        job = self.s.reserve('agent_a', 'v1', now=1, force=True)
        wrong = summary(job); wrong['observations'][0]['refs'] = ['not-a-source']
        with self.assertRaises(NotReady):
            self.s.publish(job, wrong, now=2)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM coverage').fetchone()[0], 0)

    def test_abc_rollup_races_ab_read_then_c_only(self):
        a, b = self.segment(1), self.segment(2)
        ab = self.s.claim('agent_a', 's', 't1')
        c = self.segment(3)
        job = self.s.reserve_rollup('agent_a', 'v1', now=20)
        frozen = json.dumps(ab, sort_keys=True)
        self.s.acknowledge('agent_a', 's', 't1')
        self.assertFalse(self.s.publish(job, summary(job), now=21))
        self.assertEqual(self.s.claim('agent_a', 's', 't2')['batch_ids'], [c])
        saved = self.s.db.execute('SELECT body FROM claims WHERE id=?', (ab['claim_id'],)).fetchone()[0]
        self.assertEqual(json.dumps(json.loads(saved), sort_keys=True), frozen)
        self.assertEqual({d['id'] for d in self.s.pending_save('agent_a')}, {a, b})

    def test_rollup_uses_originals_and_is_not_mutable(self):
        self.segment(1); self.segment(2)
        job = self.s.reserve_rollup('agent_a', 'v1', now=20)
        self.assertTrue(all(i['kind'] == 'segment' for i in job['inputs']))
        self.s.publish(job, summary(job), now=21)
        claim = self.s.claim('agent_a', 's', 't')
        self.assertEqual(claim['documents'][0]['kind'], 'rollup')
        self.segment(3)
        self.assertEqual(self.s.claim('agent_a', 's', 't'), claim)

    def test_agents_progress_and_save_states_are_independent(self):
        a = self.segment(1)
        other = self.s.reserve('agent_b', 'v1', force=True, now=2)
        self.s.publish(other, summary(other), now=3)
        self.s.claim('agent_a', 's', 't')
        self.assertFalse(self.s.acknowledge('agent_a', 's', 'wrong'))
        self.assertTrue(self.s.acknowledge('agent_a', 's', 't'))
        self.assertFalse(self.s.acknowledge('agent_a', 's', 't'))
        self.assertEqual(self.s.unread('agent_a'), [])
        self.assertEqual(len(self.s.unread('agent_b')), 1)
        self.assertEqual([d['id'] for d in self.s.pending_save('agent_a')], [a])
        self.assertEqual(self.s.claim('agent_a', 'new', 'new-turn', reset=True)['batch_ids'], [a])

    def test_fail_backoff_preserves_input(self):
        self.s.ingest([message(1)], now=0)
        job = self.s.reserve('agent_a', 'v1', force=True, now=1)
        self.s.fail(job, ValueError('private details'), now=2)
        self.assertIsNone(self.s.reserve('agent_a', 'v1', force=True, now=3601))
        retry = self.s.reserve('agent_a', 'v1', force=True, now=3602)
        self.assertEqual(retry['refs'], job['refs'])
        self.assertNotIn('private details', str(self.s.status()))

    def test_restart_preserves_claim_and_coverage(self):
        self.segment(1)
        before = self.s.claim('agent_a', 's', 't')
        self.s.close(); self.s = Continuity(self.root / 'state')
        self.assertEqual(self.s.claim('agent_a', 's', 't'), before)
        self.assertIsNone(self.s.reserve('agent_a', 'v1', force=True))

    def test_context_reload_continues_pages_of_already_read_unsaved_batches(self):
        for n in range(3):
            self.s.ingest([message(n*60+i) for i in range(60)], now=n)
            job = self.s.reserve('agent_a', 'v1', force=True, now=n)
            self.s.publish(job, summary(job), now=n+1)
        while self.s.unread('agent_a'):
            turn = str(len(self.s.unread('agent_a')))
            self.s.claim('agent_a', 'old', turn)
            self.s.acknowledge('agent_a', 'old', turn)
        self.assertEqual(len(self.s.pending_save('agent_a')), 3)
        first = self.s.claim('agent_a', 'new', '1', reset=True)
        self.assertGreater(first['remaining_batches'], 0)
        self.s.acknowledge('agent_a', 'new', '1')
        second = self.s.claim('agent_a', 'new', '2')
        self.assertIsNotNone(second)
        self.assertTrue(set(first['batch_ids']).isdisjoint(second['batch_ids']))


class HookCase(unittest.TestCase):
    setUp = ContinuityCase.setUp
    tearDown = ContinuityCase.tearDown
    segment = ContinuityCase.segment

    def config(self):
        self.sid = str(uuid.uuid4())
        self.transcript = self.root / 'native.jsonl'
        self.config_data = {'continuity_root': str(self.root / 'state'),
                            'sessions': {self.sid: {'agent': 'agent_b', 'cwd': str(self.root)}},
                            'transcript_roots': [str(self.root)]}
        self.event = {'session_id': self.sid, 'cwd': str(self.root), 'turn_id': 'a',
                      'transcript_path': str(self.transcript), 'hook_event_name': 'UserPromptSubmit', 'prompt': 'hello'}
        self.transcript.write_text(json.dumps({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'a'}}) + '\n'
                                   + json.dumps({'type': 'response_item', 'payload': {'role': 'user', 'content': [{'type': 'input_text', 'text': 'hello'}]}}) + '\n')

    def test_native_terminal_proof_required(self):
        self.segment(1, 'agent_b'); self.config()
        result = handle(self.config_data, self.event)
        self.assertIn('party_experiences', result['hookSpecificOutput']['additionalContext'])
        handle(self.config_data, dict(self.event, hook_event_name='Stop', last_assistant_message='done'))
        self.assertEqual(len(self.s.unread('agent_b')), 1)
        with self.transcript.open('a') as file:
            file.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'a', 'last_agent_message': 'done'}}) + '\n')
        reconcile(self.s, self.config_data)
        self.assertEqual(self.s.unread('agent_b'), [])
        self.assertEqual(len(self.s.pending_save('agent_b')), 1)

    def test_unknown_foreign_and_sidechain_do_not_claim(self):
        self.segment(1, 'agent_b'); self.config()
        for change in ({'session_id': str(uuid.uuid4())}, {'cwd': '/'}, {'agent_id': 'sub'}, {'prompt': None}):
            self.assertIsNone(handle(self.config_data, dict(self.event, **change)))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM claims').fetchone()[0], 0)

    def test_pre_append_hook_only_acknowledges_actual_matching_turn(self):
        self.segment(1, 'agent_b'); self.config()
        self.transcript.write_text('')
        self.assertIsNotNone(handle(self.config_data, self.event))
        reconcile(self.s, self.config_data)
        self.assertEqual(len(self.s.unread('agent_b')), 1)

    def test_claude_telegram_turn_needs_terminal_transcript(self):
        self.segment(1, 'agent_a'); self.config()
        self.config_data['sessions'][self.sid]['agent'] = 'agent_a'
        self.event['prompt_id'] = 'a'; self.event.pop('turn_id')
        user = {'type': 'user', 'sessionId': self.sid, 'promptId': 'a',
                'origin': {'kind': 'channel', 'server': 'plugin:telegram:telegram'},
                'message': {'content': '<channel source="plugin:telegram:telegram">hello</channel>'}}
        self.transcript.write_text(json.dumps(user) + '\n')
        self.assertIsNotNone(handle(self.config_data, self.event))
        with self.transcript.open('a') as file:
            file.write(json.dumps({'type': 'assistant', 'sessionId': self.sid, 'message': {
                'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'done'}]}}) + '\n')
        reconcile(self.s, self.config_data)
        self.assertEqual(len(self.s.unread('agent_a')), 1)
        with self.transcript.open('a') as file:
            file.write(json.dumps({'type': 'system', 'subtype': 'turn_duration', 'sessionId': self.sid}) + '\n')
        reconcile(self.s, self.config_data)
        self.assertEqual(self.s.unread('agent_a'), [])


class SummaryCapabilityCase(unittest.TestCase):
    def test_native_inner_newline_is_accepted_but_nul_is_rejected(self):
        self.assertEqual(decode_result('{"persona":"first\nsecond"}'), {'persona': 'first\nsecond'})
        with self.assertRaises(NotReady):
            decode_result('{"persona":"hidden\x00content"}')

    def test_search_not_allowed_in_summary(self):
        engine = object.__new__(NativeEngine); engine.engine = 'codex'; engine.allow_web = False
        events = [{'type': 'thread.started', 'thread_id': 'a'},
                  {'type': 'item.completed', 'item': {'id': 'w', 'type': 'web_search'}},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}'}},
                  {'type': 'turn.completed'}]
        with self.assertRaises(NotReady):
            engine.parse(events, 'a')


if __name__ == '__main__':
    unittest.main()
