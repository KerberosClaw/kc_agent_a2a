import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from test_state import registry, event
from test_native import packet
from discord_party.state import State, NotReady
from discord_party.native import Grant, write_private


class MembershipCase(unittest.TestCase):
    def test_migration_preserves_ledger_and_second_human_controls_both_bots(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bot in ('20', '30'):
                old = registry(bot)
                new = replace(old, human_ids=('10', '40'))
                root = Path(tmp) / bot
                s = State(root, old, initialize=True)
                s.synchronized(); s.ingest(event(100))
                req = s.begin(); s.finish(req, 'pass', '')
                before = s.snapshot()
                count = s.db.execute('SELECT count(*) FROM attempts').fetchone()[0]
                s.add_human(new)
                self.assertEqual(s.snapshot()['remaining'], before['remaining'])
                self.assertEqual(s.db.execute('SELECT count(*) FROM attempts').fetchone()[0], count)
                self.assertFalse(s.snapshot()['armed'])
                s.close()
                with self.assertRaises(NotReady):
                    State(root, old)
                s = State(root, new)
                s.synchronized()
                self.assertFalse(s.ingest(event(101, '50')))
                self.assertTrue(s.ingest(event(102, '40', '先停一下')))
                self.assertTrue(s.snapshot()['paused'])
                s.ingest(event(103, '10', 'ordinary'))
                self.assertTrue(s.snapshot()['paused'])
                s.ingest(event(104, '40', '繼續聊'))
                self.assertFalse(s.snapshot()['paused'])
                self.assertEqual(s.snapshot()['remaining'], 10)
                self.assertIsNotNone(s.begin())
                s.close()

    def test_migration_rejects_other_identity_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = State(Path(tmp) / 'state', registry(), initialize=True)
            try:
                with self.assertRaises(NotReady):
                    s.add_human(replace(registry(), channel_id='3', human_ids=('10', '40')))
                with self.assertRaises(ValueError):
                    replace(registry(), human_ids=('10', '10'))
            finally:
                s.close()

    def test_new_audience_history_and_names_need_explicit_grant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'grant'
            m = packet(root)
            reg = replace(registry(), human_ids=('10', '40'))
            with self.assertRaises(NotReady):
                Grant(root, 'agent_a', reg)
            m['intended_scope']['human_ids'] = ['10', '40']
            m.update(approved_at='2026-09-10T00:00:00Z', history_since='2026-09-09T00:00:00Z',
                     revision_kind='audience_expansion', supersedes='previous',
                     previous_human_ids=['10'], human_names={'10': 'Owner', '40': 'Guest'})
            write_private(root / 'manifest.json', json.dumps(m))
            with self.assertRaises(NotReady):
                Grant(root, 'agent_a', reg)
            m['history_sharing'] = {'approved_by': '10', 'human_ids': ['10', '40'],
                                    'include_existing_room_history': True}
            write_private(root / 'manifest.json', json.dumps(m))
            g = Grant(root, 'agent_a', reg)
            self.assertEqual(g.human_names['40'], 'Guest')
            shared = {'created_at': '2026-09-09T12:00:00Z'}
            self.assertEqual(g.filter_context([shared]), [shared])
            m['human_names']['50'] = 'Unregistered'
            write_private(root / 'manifest.json', json.dumps(m))
            with self.assertRaises(NotReady):
                Grant(root, 'agent_a', reg)
