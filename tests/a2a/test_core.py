import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from a2a.adapters import SyntheticAdapter
from a2a.coordinator import Coordinator, LIMITS
from a2a.notes import Mailbox
from a2a.persona import snapshot
from a2a.storage import BoundaryError, Store

DAY = '2026-09-07'


class CoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'state')
        self.mail = Mailbox(self.store)
        self.coord = Coordinator(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def send(self, **kw):
        args = dict(courier='agent_a', recipient='agent_b', source_session='sender', source_input='input', body='test note')
        return self.mail.send(**(args | kw))

    def delivered(self):
        note = self.send()
        claim = self.mail.claim('agent_b', 'receiver', 'turn')[0]
        self.mail.injected(note, claim['claim_id'])
        return dict(note_id=note, claim_id=claim['claim_id'], session='receiver', turn='turn',
                    status='success', ack_ids=[note], assistant_final='received')

    def run_id(self):
        return self.coord.start('window', DAY)

    def result(self, run, agent='agent_a', kind='chat', accept=True, **changes):
        attempt = self.coord.reserve(run, agent, kind, DAY)
        self.coord.dispatched(attempt)
        last = self.store.db.execute('SELECT request FROM messages ORDER BY ordinal DESC LIMIT 1').fetchone()
        payload = dict(request_id=attempt, native_status='success', has_topic=True, own_summary='my day') if kind == 'prepare' else dict(
            request_id=attempt, native_status='success', reply='hello', reply_to=last[0] if last else None,
            move='affect', wants_reply=True, digest_candidate={'summary': 'shared hello', 'shared_message_ids': [attempt]})
        self.coord.save_result(attempt, payload | changes)
        if accept:
            self.coord.accept(attempt)
        return attempt

    def test_note_dedup_and_validation(self):
        note = self.send()
        self.assertEqual(note, self.send(body='duplicate delivery'))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM notes').fetchone()[0], 1)
        for kwargs in ({'recipient': '../bad'}, {'recipient': 'agent_a'}, {'body': ' '}, {'body': 'a'*4001}, {'source_input': ''}):
            with self.subTest(kwargs=list(kwargs)), self.assertRaises(BoundaryError):
                self.send(**kwargs)

    def test_receipt_must_match_native_turn_and_ack(self):
        receipt = self.delivered()
        for kwargs in ({'turn': 'other'}, {'session': 'other'}, {'claim_id': 'other'}, {'status': 'failed'}, {'ack_ids': []}, {'assistant_final': ''}):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(self.mail.reconcile(**(receipt | kwargs)))
                self.assertEqual(self.mail.status(receipt['note_id']), 'delivered')
        self.assertTrue(self.mail.reconcile(**receipt))
        self.assertEqual(self.mail.status(receipt['note_id']), 'burned')
        self.assertFalse(list((self.store.root/'mail').rglob('*.json')))

    def test_confirmed_failure_retry_preserves_id(self):
        receipt = self.delivered()
        with self.assertRaises(BoundaryError):
            self.mail.retry_failed(receipt['note_id'], receipt['claim_id'], native_status='unknown')
        self.mail.retry_failed(receipt['note_id'], receipt['claim_id'], native_status='failed')
        claim = self.mail.claim('agent_b', 'receiver', 'next')[0]
        self.assertEqual(claim['id'], receipt['note_id'])
        self.assertNotEqual(claim['claim_id'], receipt['claim_id'])

    def test_cancel_before_injection_only(self):
        note = self.send()
        self.assertTrue(self.mail.cancel(note))
        self.assertEqual(self.mail.status(note), 'cancelled')
        self.assertFalse(list((self.store.root/'mail').rglob('*.json')))
        receipt = self.delivered_other()
        self.assertFalse(self.mail.cancel(receipt))

    def delivered_other(self):
        note = self.send(source_input='second')
        claim = self.mail.claim('agent_b', 'r', 't')[0]
        self.mail.injected(note, claim['claim_id'])
        return note

    def test_cancel_survives_unlink_failure(self):
        note = self.send()
        with patch.object(Path, 'unlink', side_effect=OSError('injected failure')):
            with self.assertRaises(OSError): self.mail.cancel(note)
        self.assertEqual(self.mail.status(note), 'cancelled')
        self.assertEqual(self.mail.claim('agent_b', 'new', 'new'), [])
        self.mail.cleanup(note)
        self.assertFalse(list((self.store.root/'mail').rglob('*.json')))
        self.assertEqual(self.mail.status(note), 'cancelled')

    def test_ack_survives_unlink_failure(self):
        receipt = self.delivered()
        with patch.object(Path, 'unlink', side_effect=OSError('injected failure')):
            with self.assertRaises(OSError):
                self.mail.reconcile(**receipt)
        self.assertEqual(self.mail.status(receipt['note_id']), 'acknowledged')
        self.assertEqual(self.mail.claim('agent_b', 'new', 'new'), [])
        self.mail.cleanup(receipt['note_id'])
        self.assertEqual(self.mail.status(receipt['note_id']), 'burned')

    def test_claim_batch_character_and_count_limits(self):
        for i in range(7):
            self.send(source_input=str(i), body='x'*4000)
        self.assertEqual(len(self.mail.claim('agent_b', 'r', 't')), 3)
        for i in range(7):
            self.mail.send(courier='agent_b', recipient='agent_a', source_session='s', source_input=str(i), body='x')
        self.assertEqual(len(self.mail.claim('agent_a', 'r', 't')), 5)

    def test_crash_after_rename_reclaims_without_duplicate(self):
        note = self.send()
        row = self.store.db.execute('SELECT * FROM notes WHERE id=?', (note,)).fetchone()
        claimed = self.mail.path(row, 'claimed')
        claimed.parent.mkdir(parents=True)
        code = "from pathlib import Path; import os,sys; from a2a.storage import Store; s=Store(Path(sys.argv[1])); s.db.execute('BEGIN IMMEDIATE'); Path(sys.argv[2]).replace(Path(sys.argv[3])); os._exit(23)"
        p = subprocess.run([sys.executable, '-c', code, str(self.store.root), str(self.mail.path(row, 'new')), str(claimed)])
        self.assertEqual(p.returncode, 23)
        claims = self.mail.claim('agent_b', 'r', 't')
        self.assertEqual([c['id'] for c in claims], [note])
        self.assertEqual(self.mail.claim('agent_b', 'r2', 't2'), [])

    def test_parallel_claims_do_not_duplicate(self):
        self.send()
        code = "from pathlib import Path; import sys,json; from a2a.storage import Store; from a2a.notes import Mailbox; s=Store(Path(sys.argv[1])); print(json.dumps(Mailbox(s).claim('agent_b',sys.argv[2],'t')))"
        workers = [subprocess.Popen([sys.executable, '-c', code, str(self.store.root), str(i)], stdout=subprocess.PIPE, text=True) for i in range(2)]
        claims = []
        for p in workers:
            out, _ = p.communicate(timeout=10)
            self.assertEqual(p.returncode, 0)
            claims.extend(json.loads(out))
        self.assertEqual(len(claims), 1)

    def test_budget_10_chat_plus_preparation(self):
        run = self.run_id()
        self.result(run, kind='prepare')
        for _ in range(10):
            self.result(run)
        with self.assertRaises(BoundaryError):
            self.coord.reserve(run, 'agent_a', 'chat', DAY)
        with self.assertRaises(BoundaryError):
            self.coord.reserve(run, 'agent_a', 'prepare', DAY)
        self.result(run, agent='agent_b')

    def test_retries_chain_but_stay_bounded_and_single_use(self):
        # A refused call may be resent, and a resend that is refused again may itself be resent,
        # so retries chain. The budget still caps the chain and no attempt is ever retried twice.
        run = self.run_id()
        original = self.coord.reserve(run, 'agent_a', 'chat', DAY)
        self.coord.dispatched(original)
        self.coord.fail(original, confirmed=True)
        parent = original
        for _ in range(LIMITS['retry']):
            retry = self.coord.reserve(run, 'agent_a', 'retry', DAY, parent=parent)
            self.coord.dispatched(retry)
            self.coord.fail(retry, confirmed=True)
            with self.assertRaises(BoundaryError):
                self.coord.reserve(run, 'agent_a', 'retry', DAY, parent=parent)
            parent = retry
        with self.assertRaises(BoundaryError):
            self.coord.reserve(run, 'agent_a', 'retry', DAY, parent=parent)
        # The whole chain still resolves to the purpose of the original attempt.
        row = self.store.db.execute('SELECT * FROM attempts WHERE id=?', (parent,)).fetchone()
        self.assertEqual(self.coord.purpose(row), 'chat')

    def test_retry_budget_is_separate_from_chat_and_prepare(self):
        run = self.run_id()
        for _ in range(LIMITS['retry']):
            original = self.coord.reserve(run, 'agent_a', 'chat', DAY)
            self.coord.dispatched(original)
            self.coord.fail(original, confirmed=True)
            self.coord.fail(self.coord.reserve(run, 'agent_a', 'retry', DAY, parent=original), confirmed=True)
        spent = self.coord.reserve(run, 'agent_a', 'chat', DAY)
        self.coord.dispatched(spent)
        self.coord.fail(spent, confirmed=True)
        with self.assertRaises(BoundaryError):
            self.coord.reserve(run, 'agent_a', 'retry', DAY, parent=spent)
        # chat budget is untouched by the spent retries
        self.assertTrue(self.coord.reserve(run, 'agent_a', 'chat', DAY))

    def test_unknown_not_retryable(self):
        run = self.run_id()
        attempt = self.coord.reserve(run, 'agent_a', 'chat', DAY)
        self.coord.fail(attempt, confirmed=False)
        with self.assertRaises(BoundaryError):
            self.coord.reserve(run, 'agent_a', 'retry', DAY, parent=attempt)
        with self.assertRaises(BoundaryError):
            self.coord.reserve(run, 'agent_b', 'chat', DAY)

    def test_day_cannot_be_forged_to_reset_budget(self):
        run = self.run_id()
        with self.assertRaises(BoundaryError):
            self.coord.reserve(run, 'agent_a', 'chat', '2026-09-08')

    def test_single_inflight_and_persistent_window_guard(self):
        run = self.run_id()
        self.coord.reserve(run, 'agent_a', 'chat', DAY)
        second = Store(self.store.root)
        try:
            with self.assertRaises(BoundaryError):
                Coordinator(second).reserve(run, 'agent_b', 'chat', DAY)
            with self.assertRaises(BoundaryError):
                Coordinator(second).start('other-window', DAY)
        finally:
            second.close()

    def test_os_lock_excludes_second_process(self):
        code = "from pathlib import Path; import sys; from a2a.storage import Store,BoundaryError; s=Store(Path(sys.argv[1]));\ntry:\n with s.coordinator_lock(): sys.exit(2)\nexcept BoundaryError: sys.exit(0)"
        with self.store.coordinator_lock():
            p = subprocess.run([sys.executable, '-c', code, str(self.store.root)])
        self.assertEqual(p.returncode, 0)

    def test_saved_result_recovers_after_process_exit(self):
        run = self.run_id()
        attempt = self.coord.reserve(run, 'agent_a', 'chat', DAY)
        self.coord.dispatched(attempt)
        payload = dict(request_id=attempt, native_status='success', reply='hello', reply_to=None, move='affect', wants_reply=True,
                       digest_candidate={'summary': 'hello', 'shared_message_ids': [attempt]})
        code = "from pathlib import Path; import os,sys,json; from a2a.storage import Store; from a2a.coordinator import Coordinator; s=Store(Path(sys.argv[1])); Coordinator(s).save_result(sys.argv[2],json.loads(sys.argv[3])); os._exit(29)"
        p = subprocess.run([sys.executable, '-c', code, str(self.store.root), attempt, json.dumps(payload)])
        self.assertEqual(p.returncode, 29)
        self.coord.accept(attempt)
        self.coord.accept(attempt)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM messages').fetchone()[0], 1)

    def test_stop_discards_late_result(self):
        run = self.run_id()
        attempt = self.result(run, accept=False)
        self.coord.stop(run)
        with self.assertRaises(BoundaryError):
            self.coord.accept(attempt)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)

    def test_payload_rejects_foreign_refs_and_wrong_reply(self):
        for changes in ({'request_id':'foreign'}, {'native_status':'error'}, {'reply_to':'foreign'}, {'reply':'x'*1201},
                        {'digest_candidate': {'summary':'x', 'shared_message_ids':['private-event']}}):
            with self.subTest(changes=changes):
                # Separate temporary state per invalid fixture.
                with tempfile.TemporaryDirectory() as tmp:
                    s = Store(Path(tmp)); c = Coordinator(s)
                    run = c.start('w', DAY); a = c.reserve(run, 'agent_a', 'chat', DAY); c.dispatched(a)
                    payload = dict(request_id=a, native_status='success', reply='hello', reply_to=None, move='affect', wants_reply=True,
                                   digest_candidate={'summary':'x', 'shared_message_ids':[a]})
                    try:
                        with self.assertRaises(BoundaryError): c.save_result(a, payload | changes)
                    finally: s.close()

    def test_cursors_partial_and_idempotent_export(self):
        run = self.run_id()
        for agent in ('agent_a', 'agent_b'): self.result(run, agent=agent, kind='prepare')
        self.result(run)
        self.coord.finish(run, 'partial', {'agent_a':'m1', 'agent_b':'z1'})
        self.coord.finish(run, 'incorrect-replay', {'agent_a':'m2'})
        self.assertEqual([tuple(r) for r in self.store.db.execute('SELECT * FROM cursors')], [('agent_a','m1')])
        path = self.coord.export(run, self.root/'exports'); original = path.read_bytes()
        self.coord.export(run, self.root/'exports')
        self.assertEqual(original, path.read_bytes())
        self.assertIsNone(json.loads(original)['digests']['agent_b'])

    def test_preparation_only_failure_does_not_consume_material(self):
        run = self.run_id(); self.result(run, kind='prepare')
        self.coord.finish(run, 'failed', {'agent_a':'m1'})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM cursors').fetchone()[0], 0)

    def test_both_no_topic_advances_both_cursors(self):
        run = self.run_id()
        for agent in ('agent_a', 'agent_b'): self.result(run, agent=agent, kind='prepare', has_topic=False)
        self.coord.finish(run, 'skipped_no_topic', {'agent_a':'m1', 'agent_b':'z1'})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM cursors').fetchone()[0], 2)

    def test_affect_is_not_noop_and_two_noops_end(self):
        run = self.run_id(); self.result(run); self.result(run, agent='agent_b')
        self.assertFalse(self.coord.should_end(run))
        self.result(run, move='noop'); self.result(run, agent='agent_b', move='noop')
        self.assertTrue(self.coord.should_end(run))


class PersonaTests(unittest.TestCase):
    def test_full_active_patches_recent_summary_archive_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'patches/_archive').mkdir(parents=True); (root/'journal').mkdir()
            (root/'base.md').write_text('FULL BASELINE')
            for name in ('001','002','099'): (root/'patches'/f'{name}.md').write_text(f'PATCH {name}')
            (root/'patches/_archive/old.md').write_text('DO NOT LOAD')
            (root/'journal/20260906.md').write_text('SUMMARY\nPRIVATE FULL JOURNAL')
            (root/'journal/20260901.md').write_text('OLD JOURNAL')
            data = snapshot(root, 'base.md', date(2026,9,7))
            self.assertEqual(len(data['manifest']), 5)
            self.assertEqual(data['content'], 'FULL BASELINE\n\nPATCH 001\n\nPATCH 002\n\nPATCH 099\n\nSUMMARY')
            with self.assertRaises(BoundaryError):
                snapshot(root,'base.md',date(2026,9,7),after_read=lambda:(root/'base.md').write_text('CHANGED'))
            (root/'patches/link.md').symlink_to(root/'base.md')
            with self.assertRaises(BoundaryError): snapshot(root,'base.md',date(2026,9,7))


class AdapterTests(unittest.TestCase):
    def test_subprocess_fault_modes(self):
        adapter = SyntheticAdapter()
        for mode, error in (('timeout','timeout'),('crash','adapter failed'),('invalid_json','invalid adapter JSON')):
            with self.subTest(mode=mode), self.assertRaisesRegex(BoundaryError, error):
                adapter.call({'request_id':'test','purpose':'prepare','fixture_mode':mode}, timeout=0.2)
        self.assertEqual(adapter.call({'request_id':'test','purpose':'prepare','own_material':['x']})['native_status'], 'success')


if __name__ == '__main__': unittest.main()
