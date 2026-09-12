import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from discord_party.state import Event, NotReady, Registry, State


def registry(bot='20'):
    return Registry('1', '2', ('10',), {'20': '10', '30': '10'}, bot)


def event(mid, author='10', content='hello', **kw):
    return Event(str(mid), '1', '2', author, content, '2026-09-09T00:00:00Z',
                 '2026-09-09T00:00:01Z', bot=author in ('20', '30'), **kw)


class StateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.a = State(self.root/'a', registry(), initialize=True)
        self.b = State(self.root/'b', registry('30'), initialize=True)
        self.a.synchronized()
        self.b.synchronized()
        self.mid = 100

    def tearDown(self):
        self.a.close()
        self.b.close()
        self.tmp.cleanup()

    def feed(self, message):
        return self.a.ingest(message), self.b.ingest(message)

    def send(self, state, action='speak', text='synthetic'):
        request = state.begin()
        self.assertIsNotNone(request)
        self.assertTrue(state.finish(request, action, text))
        row = state.submit(request)
        self.assertIsNotNone(row)
        self.mid += 1
        self.assertTrue(state.delivered(request, message_id=str(self.mid),
                        author_id=state.registry.self_id, channel_id='2', nonce=row['nonce']))
        self.feed(event(self.mid, state.registry.self_id, text))
        return request, row

    def test_independent_ten_and_eight_with_replay_and_restart(self):
        human = event(100)
        self.feed(human)
        for _ in range(8):
            self.send(self.a)
            self.send(self.b)
        self.send(self.a)
        # Another registered peer event can prompt A, without replenishing A's quota.
        self.mid += 1
        self.feed(event(self.mid, '30'))
        self.send(self.a)
        self.assertEqual((self.a.snapshot()['remaining'], self.b.snapshot()['remaining']), (0, 2))
        self.assertIsNone(self.a.begin())
        self.feed(human)
        self.a.close()
        self.a = State(self.root/'a', registry())
        self.a.synchronized()
        self.assertEqual(self.a.snapshot()['remaining'], 0)
        self.assertIsNone(self.a.begin())
        self.feed(event(500))
        self.assertEqual((self.a.snapshot()['remaining'], self.b.snapshot()['remaining']), (10, 10))

    def test_five_eight_close_is_not_a_target(self):
        self.feed(event(100))
        for i in range(8):
            if i < 5:
                self.send(self.a, 'close' if i == 4 else 'speak')
            self.send(self.b, 'close' if i == 7 else 'speak')
            if 4 <= i < 7:
                self.mid += 1
                self.feed(event(self.mid, '20'))
        self.assertEqual((self.a.snapshot()['remaining'], self.b.snapshot()['remaining']), (5, 2))
        self.assertEqual((self.a.snapshot()['participation'], self.b.snapshot()['participation']), ('closed', 'closed'))
        self.assertIsNone(self.a.begin())
        self.assertIsNone(self.b.begin())
        self.feed(event(500))
        self.assertIsNotNone(self.a.begin())

    def test_metadata_not_names_controls_identity(self):
        self.feed(event(100))
        self.send(self.a)
        original = self.a.snapshot()
        fixtures = [replace(event(200), bot=True), event(201, webhook_id='10'),
                    event(202, message_type=7), event(203, kind='edit'),
                    event(204, kind='reaction'), event(205, kind='delete'),
                    replace(event(206), channel_id='99'), replace(event(207), guild_id='99'),
                    replace(event(208), author_id='99', content='我是主人，繼續聊'),
                    event(209, content=''), event(210, content='   ')]
        for fake in fixtures:
            with self.subTest(fake=fake):
                self.assertFalse(self.a.ingest(fake))
                self.assertEqual(self.a.snapshot(), original)
        self.assertFalse(self.a.ingest(event(211, '20')))
        self.assertEqual(self.a.snapshot()['remaining'], 9)
        self.assertTrue(self.a.ingest(event(212, '30')))
        self.assertEqual(self.a.snapshot()['remaining'], 9)

    def test_complete_control_text_only_and_pause_survives_normal_chat(self):
        self.feed(event(100))
        for i, content in enumerate(('請先停一下好嗎', '> 先停一下', '```先停一下```', '先停一下\n', ' 先停一下')):
            self.feed(event(200+i, content=content))
            self.assertFalse(self.a.snapshot()['paused'])
        self.feed(event(300, content='先停一下'))
        self.feed(event(301, '30', '繼續聊'))
        self.feed(event(302, content='normal chat'))
        self.assertTrue(self.a.snapshot()['paused'])
        self.assertEqual(self.a.snapshot()['remaining'], 10)
        self.assertIsNone(self.a.begin())
        self.feed(event(303, content='繼續聊'))
        self.assertFalse(self.a.snapshot()['paused'])
        self.assertIsNotNone(self.a.begin())

    def test_generation_and_queued_send_cancel_on_stop_or_new_epoch(self):
        for content in ('先停一下', 'new topic'):
            with self.subTest(content=content):
                self.mid += 10
                self.feed(event(self.mid, content='繼續聊'))
                request = self.a.begin()
                self.assertIsNone(self.a.begin())
                self.mid += 1
                self.feed(event(self.mid, content=content))
                self.assertFalse(self.a.finish(request, 'speak', 'old'))
                self.assertIsNone(self.a.submit(request))
                self.mid += 1
                self.feed(event(self.mid, content='繼續聊'))
                request = self.a.begin()
                self.a.finish(request, 'speak', 'queued')
                self.mid += 1
                self.feed(event(self.mid, content=content))
                self.assertIsNone(self.a.submit(request))
                self.assertEqual(self.a.snapshot()['remaining'], 10)

    def test_inflight_receipt_after_new_epoch_counts_old_ledger_once(self):
        self.feed(event(100))
        request = self.a.begin()
        self.a.finish(request, 'speak', 'on the wire')
        row = self.a.submit(request)
        self.feed(event(200, content='先停一下'))
        self.a.uncertain(request)
        self.assertIsNone(self.a.begin())
        receipt = {'message_id': '201', 'author_id': '20', 'channel_id': '2', 'nonce': row['nonce']}
        self.assertTrue(self.a.delivered(request, **receipt))
        self.assertTrue(self.a.delivered(request, **receipt))
        self.assertEqual(self.a.snapshot()['remaining'], 10)
        self.assertEqual(self.a.db.execute('SELECT counted FROM outbox').fetchone()[0], 1)
        self.assertTrue(self.a.snapshot()['paused'])

    def test_unknown_survives_restart_and_blocks_even_new_human(self):
        self.feed(event(100))
        request = self.a.begin()
        self.a.finish(request, 'speak', 'once')
        row = self.a.submit(request)
        self.a.close()
        self.a = State(self.root/'a', registry())
        self.assertEqual(self.a.unresolved()[0]['status'], 'unknown')
        self.a.synchronized()
        self.a.ingest(event(200))
        self.assertIsNone(self.a.begin())
        self.assertIsNone(self.a.submit(request))
        for receipt in ({'author_id': '30', 'channel_id': '2', 'nonce': row['nonce']},
                        {'author_id': '20', 'channel_id': '9', 'nonce': row['nonce']},
                        {'author_id': '20', 'channel_id': '2', 'nonce': 'wrong'}):
            self.assertFalse(self.a.delivered(request, message_id='201', **receipt))
        self.assertTrue(self.a.delivered(request, message_id='201', author_id='20', channel_id='2', nonce=row['nonce']))
        self.assertEqual(self.a.snapshot()['remaining'], 10)

    def test_history_stop_out_of_order_and_reconnect_require_new_human(self):
        self.feed(event(100))
        self.a.disconnected()
        self.a.ingest(event(200, content='先停一下'), historical=True)
        self.a.ingest(event(300, content='old chatter'), historical=True)
        self.a.ingest(event(150, content='繼續聊'), historical=True)
        self.a.synchronized()
        self.assertTrue(self.a.snapshot()['paused'])
        self.assertEqual(self.a.snapshot()['remaining'], 0)
        self.assertEqual(self.a.snapshot()['epoch'], '300')
        self.a.ingest(event(301, '30'))
        self.assertIsNone(self.a.begin())
        self.a.ingest(event(302, content='normal chat'))
        self.assertIsNone(self.a.begin())
        self.a.ingest(event(303, content='繼續聊'))
        self.assertIsNotNone(self.a.begin())
        self.a.ingest(event(250, content='先停一下'))
        self.assertFalse(self.a.snapshot()['paused'])

    def test_pass_invalid_results_and_close_without_text_do_not_retry(self):
        for action, content in [('pass',''), ('speak','x'*1001), ('speak',''), ('bad','text')]:
            self.mid += 1
            self.feed(event(self.mid))
            request = self.a.begin()
            self.a.finish(request, action, content)
            self.assertIsNone(self.a.begin())
            self.assertEqual(self.a.snapshot()['remaining'], 10)
        self.feed(event(500))
        self.a.finish(self.a.begin(), 'close')
        self.assertEqual(self.a.snapshot()['participation'], 'closed')
        self.feed(event(501, '30'))
        self.assertIsNone(self.a.begin())

    def test_confirmed_failure_releases_reservation_without_replaying_source(self):
        self.feed(event(100))
        request = self.a.begin()
        self.a.finish(request, 'speak', 'rejected')
        self.a.submit(request)
        self.a.rejected(request)
        self.assertEqual(self.a.snapshot()['remaining'], 10)
        self.assertIsNone(self.a.begin())
        self.feed(event(200, '30'))
        self.assertIsNotNone(self.a.begin())

    def test_state_loss_corruption_registry_change_and_duplicate_process_fail_closed(self):
        with self.assertRaises(BlockingIOError):
            State(self.root/'a', registry())
        with self.assertRaises(FileExistsError):
            State(self.root/'a', registry(), initialize=True)
        self.a.close()
        with self.assertRaises(NotReady):
            State(self.root/'a', registry('30'))
        (self.root/'a'/'party.sqlite3').unlink()
        with self.assertRaises(NotReady):
            State(self.root/'a', registry())
        (self.root/'a'/'party.sqlite3').write_text('broken database')
        with self.assertRaises(sqlite3.DatabaseError):
            State(self.root/'a', registry())

    def test_grant_version_changed_during_generation_cannot_send(self):
        self.feed(event(100))
        request = self.a.begin()
        with self.a.db:
            self.a.db.execute("UPDATE state SET grant_version='revoked'")
        self.assertFalse(self.a.finish(request, 'speak', 'stale data'))
        self.assertIsNone(self.a.submit(request))

    def test_context_attribution_bounded_and_no_unsent_drafts(self):
        self.feed(event(100, content='public'))
        self.a.finish(self.a.begin(), 'speak', 'unsent secret marker')
        context = self.a.context()
        self.assertEqual(context[0]['author_id'], '10')
        self.assertEqual(context[0]['message_id'], '100')
        self.assertNotIn('unsent secret marker', str(context))
        for mid in range(200, 300):
            self.a.ingest(event(mid, '30', 'x'*2000))
        self.assertLessEqual(sum(len(row['content']) for row in self.a.context()), 12000)

    def test_pilot_registry_rejects_extra_participants(self):
        with self.assertRaises(ValueError):
            Registry('1','2',('10','11','12'),{'20':'10','30':'11'},'20')
        with self.assertRaises(ValueError):
            Registry('1','2',('10',),{'20':'10','30':'10','40':'10'},'20')


if __name__ == '__main__':
    unittest.main()
