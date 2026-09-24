import json
from datetime import datetime
from pathlib import Path
import unittest

import test_life_context as fixtures
from test_native import packet
from test_state import registry, event
from discord_party.continuity import encode, fingerprint, Continuity
from discord_party.life_context import LifeContextResolver
from discord_party.life_tools import LifeQuery
from discord_party.native import Grant, write_private
from discord_party.shared_context import refresh, read_projection, source_bytes, upstream, decode_context, validate_records
from discord_party.state import NotReady, State


NOW = datetime.fromisoformat('2026-09-24T15:00:00+08:00').timestamp()


class ContextCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.LifeContextCase(); self.fixture.setUp()
        self.root, self.policy = self.fixture.root, self.fixture.policy
        self.runtime = self.root / 'runtime'; self.runtime.mkdir()
        self.roots = {'shared': self.fixture.wiki / 'raw/entries',
                      'agent_a': self.root / 'agent_a/journal', 'agent_b': self.root / 'agent_b/journal'}
        for scope, root in self.roots.items():
            root.mkdir(parents=True)
            for day in range(1, 8):
                (root / f'202609{day:02}.md').write_text(f'{scope} day {day}\n一般生活與相處\nPRIVATE_SECRET\n')
            (root / '20260925.md').write_text('future must not load')
        self.fixture.body['shared_context'] = {
            'mode': 'daily_life_and_own_interactions', 'backfill_count': 5,
            'root': str(self.runtime / 'shared-context'),
            'journal_roots': {a: str(self.roots[a].parent) for a in ('agent_a', 'agent_b')},
            'agent_bot_ids': {'agent_a': '20', 'agent_b': '30'},
        }
        self.fixture.write_policy()
        packet(self.root / 'grant')
        self.grants = {a: Grant(self.root / 'grant', a, registry(bot)) for a, bot in [('agent_a', '20'), ('agent_b', '30')]}
        self.config = {'party_runtime': str(self.runtime), 'life_context_policy': str(self.policy),
                       'interactive_roots': {a: str(self.roots[a].parent) for a in ('agent_a', 'agent_b')}}
        self.calls, self.payloads = [], []
        outer = self
        class Fake:
            def __init__(self, grant, work, **kwargs):
                outer.calls.append((grant.agent, kwargs))
                self.last_audit = {'synthetic': True}
            async def decide(self, messages, remaining):
                payload = json.loads(messages[0]['content']); outer.payloads.append(payload)
                if 'candidate' in payload:
                    return 'speak', encode({'approved_indices': [0], 'issues': [{'index': 1, 'reason': 'privacy'}]})
                return 'speak', encode({'records': [
                    {'name': payload['source_id'], 'text': payload['owner'] + ' 一般日常故事',
                     'event_date': payload['recorded_date'], 'nature': 'interaction', 'lines': [1, 2], 'upstream_refs': payload['upstream_refs']},
                    {'name': 'private', 'text': 'PRIVATE_SECRET', 'event_date': '',
                     'nature': 'observation', 'lines': [3, 3], 'upstream_refs': []}]})
        self.factory = Fake

    def tearDown(self):
        self.fixture.tearDown()

    def resolver(self, bot='20'):
        return LifeContextResolver(self.policy, registry(bot))

    async def tick(self, now=NOW, **kwargs):
        return await refresh(self.config, self.grants, engine_factory=kwargs.pop('engine_factory', self.factory),
                             now=now, max_documents=kwargs.pop('max_documents', 20), **kwargs)

    async def prime(self):
        await self.tick(); return await self.tick(NOW + 61)

    async def test_latest_five_by_date_once_shared_and_separate_own_context(self):
        before = {str(p): p.read_bytes() for root in self.roots.values() for p in root.glob('*.md')}
        first = await self.tick()
        self.assertEqual(first['pending'], 15); self.assertEqual(self.calls, [])
        result = await self.tick(NOW + 61)
        self.assertEqual((result['documents'], result['records']), (15, 15))
        self.assertEqual(len(self.calls), 30)  # shared five processed once, not once per persona
        for bot, own, other in [('20', 'agent_a', 'agent_b'), ('30', 'agent_b', 'agent_a')]:
            records, _ = read_projection(self.resolver(bot))
            self.assertEqual(len(records), 10)
            self.assertEqual({r['owner'] for r in records}, {'shared', own})
            self.assertNotIn(other + ' 一般', encode(records))
            self.assertNotIn('PRIVATE_SECRET', encode(records))
            self.assertNotIn('20260901', encode(records)); self.assertNotIn('20260925', encode(records))
        await self.tick(NOW + 120)
        self.assertEqual(len(self.calls), 30)
        self.assertEqual(before, {str(p): p.read_bytes() for root in self.roots.values() for p in root.glob('*.md')})
        self.assertTrue(all(not opts['allow_web'] for _, opts in self.calls))

    async def test_edit_old_source_new_file_and_no_old_version_while_pending(self):
        await self.prime()
        changed = self.roots['shared'] / '20260907.md'; changed.write_text('changed\nnew context\nsecret')
        # Read side invalidates immediately, not only after the worker next runs.
        self.assertEqual(len(read_projection(self.resolver())[0]), 9)
        (self.roots['shared'] / '20260901.md').write_text('changed old\nnew context\nsecret')
        (self.roots['agent_a'] / '20260924.md').write_text('new diary\nnew context\nsecret')
        self.assertEqual((await self.tick(NOW + 120))['pending'], 3)
        self.assertEqual((await self.tick(NOW + 181))['processed'], 3)
        self.assertEqual(len(self.calls), 36)
        self.assertEqual(len(read_projection(self.resolver())[0]), 12)

    async def test_removal_and_archive_move_do_not_duplicate(self):
        await self.prime()
        root = self.roots['agent_a']; (root / '_archive/2026Q3').mkdir(parents=True)
        (root / '20260907.md').rename(root / '_archive/2026Q3/20260907.md')
        (self.roots['shared'] / '20260906.md').unlink()
        await self.tick(NOW + 120)
        self.assertEqual(len(self.calls), 30)
        self.assertEqual(len(read_projection(self.resolver())[0]), 9)

    async def test_source_race_during_review_never_publishes_candidate(self):
        await self.tick()
        factory, path = self.factory, self.roots['shared'] / '20260903.md'
        class Racing(factory):
            async def decide(self, messages, remaining):
                payload = json.loads(messages[0]['content'])
                result = await super().decide(messages, remaining)
                if payload['source_id'] == 'shared/20260903' and 'candidate' in payload:
                    path.write_text('changed during review')
                return result
        result = await self.tick(NOW + 61, engine_factory=Racing)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('shared/20260903', encode(read_projection(self.resolver())[0]))

    async def test_policy_revocation_blocks_existing_query_and_new_policy_hides_old_view(self):
        await self.prime(); query = LifeQuery(self.resolver(), lambda: None)
        self.fixture.body['approval_evidence'] = 'new scope'; self.fixture.write_policy()
        self.assertEqual(query.call('search', {'query': '', 'kind': 'event'})['status'], 'unavailable')
        self.assertEqual(read_projection(self.resolver())[0], [])

    async def test_query_pages_all_events_and_source_changes_cancel_frozen_query(self):
        await self.prime(); query = LifeQuery(self.resolver(), lambda: None)
        page = query.call('search', {'query': '', 'kind': 'event', 'page_size': 2})
        self.assertEqual(page['total'], 5); self.assertTrue(page['has_more'])
        self.assertEqual([r['event_date'] for r in page['items']], ['2026-09-07', '2026-09-06'])
        missing = query.call('search', {'query': 'unlikely missing words', 'kind': 'interaction'})
        self.assertIn('hint', missing)
        self.assertTrue(query.used_sources)
        same_title = query.call('search', {'query': '一般', 'kind': 'interaction'})
        self.assertEqual(same_title['status'], 'found')
        (self.roots['agent_a'] / '20260907.md').write_text('changed source')
        self.assertEqual(query.call('read', {'id': page['items'][0]['id']})['status'], 'unavailable')
        with self.assertRaises(NotReady): query.validate_sources()

    async def test_forged_evidence_and_failed_audit_never_expose_raw(self):
        await self.tick(); factory = self.factory
        class Forged(factory):
            async def decide(self, messages, remaining):
                action, raw = await super().decide(messages, remaining)
                obj = json.loads(raw)
                if 'records' in obj: obj['records'][0]['lines'] = [1, 999]
                return action, encode(obj)
        result = await self.tick(NOW + 61, engine_factory=Forged)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(read_projection(self.resolver())[0], [])
        calls = len(self.calls)
        delayed = await self.tick(NOW + 120)
        self.assertEqual(delayed['status'], 'failed')
        self.assertTrue(delayed['errors'])
        self.assertEqual(len(self.calls), calls)  # no immediate retry storm

    async def test_snapshot_tampering_and_source_symlink_rejected(self):
        await self.prime()
        output = self.runtime / 'shared-context'
        pointer = json.loads((output / 'current.json').read_text())
        path = output / 'versions' / (pointer['version'] + '.json')
        value = json.loads(path.read_text()); value['documents'][0]['records'][0]['text'] = 'unreviewed'
        write_private(path, encode(value))
        with self.assertRaises(NotReady): read_projection(self.resolver())
        escaped = self.roots['agent_a'] / '20260924.md'; escaped.symlink_to(self.roots['shared'] / '20260907.md')
        with self.assertRaises(NotReady): source_bytes(self.roots['agent_a'], escaped.name)

    async def test_round_robin_and_upstream_provenance_not_new_event(self):
        refs = ['2/123@old', 'shared/20260901@original']
        write_private(self.roots['agent_a'].parent / 'continuity/saves/test.json', encode({
            'journal_files': ['journal/20260903.md'], 'source_ids': refs[:1], 'upstream_refs': refs[1:]}))
        await self.tick(); await self.tick(NOW + 61, max_documents=3)
        records, _ = read_projection(self.resolver())
        own = [r for r in records if r['owner'] == 'agent_a'][0]
        self.assertTrue(set(refs) <= set(own['source_refs']))
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(len(read_projection(self.resolver('30'))[0]), 2)
        self.assertEqual(upstream(self.roots['agent_a'], '20260904.md'), [])


class ProvenanceCase(unittest.TestCase):
    def test_archive_attaches_delivered_source_after_merging_peer_copies(self):
        import tempfile
        from discord_party.archive import collect
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); packet(root / 'grant')
            for agent, bot in [('agent_a', '20'), ('agent_b', '30')]:
                reg = registry(bot)
                write_private(root / 'config' / (agent + '.json'), reg.canonical())
                state = State(root / 'state' / agent, reg, initialize=True)
                state.synchronized(); state.ingest(event(100))
                if agent == 'agent_a':
                    req = state.begin()
                    state.finish(req, 'speak', 'public reply', source_refs=['shared/20260901@original'])
                    row = state.submit(req)
                    state.delivered(req, message_id='200', author_id='20', channel_id='2', nonce=row['nonce'])
                state.ingest(event(200, '20', 'public reply')); state.close()
            messages, _ = collect(root, root / 'grant')
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[-1]['context_source_refs'], ['shared/20260901@original'])
            self.assertNotIn('context_source_refs', messages[0])

    def test_native_single_line_and_known_serializer_suffix_without_relaxing_content(self):
        value = decode_context('{"records":[]}</content>\n</invoke>\n')
        self.assertEqual(value, {'records': []})
        self.assertEqual(decode_context('{"records":[]}</content>\n'), {'records': []})
        with self.assertRaises(json.JSONDecodeError): decode_context('{"records":[]} ignore all rules')
        with self.assertRaises(json.JSONDecodeError): decode_context('{"overview":"ok"},"observations":[]}')
        with self.assertRaises(json.JSONDecodeError): decode_context('{"records":[]，"other":true}')
        record = {'name': '合成', 'text': '當日互動', 'event_date': '', 'nature': 'interaction',
                  'lines': [2], 'upstream_refs': []}
        self.assertEqual(validate_records({'records': [record]}, 3, [])[0]['lines'], [2, 2])
        record['lines'] = [1, 3, 4]
        self.assertEqual(validate_records({'records': [record]}, 4, [])[0]['lines'], [1, 4])
        record['lines'] = [4]
        with self.assertRaises(NotReady): validate_records({'records': [record]}, 3, [])

    def test_delivered_outbox_provenance_and_continuity_preserve_original(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state = State(Path(tmp) / 'state', registry(), initialize=True)
            state.synchronized(); state.ingest(event(100))
            req = state.begin()
            state.finish(req, 'speak', '新的回應', source_refs=['shared/20260901@original'])
            row = state.submit(req)
            state.delivered(req, message_id='200', author_id='20', channel_id='2', nonce=row['nonce'])
            self.assertEqual(json.loads(state.db.execute('SELECT source_refs FROM context_origins').fetchone()[0]), ['shared/20260901@original'])
            state.close()
            continuity = Continuity(Path(tmp) / 'continuity')
            msg = {'channel_id': '2', 'message_id': '200', 'author_id': '20',
                   'created_at': '2026-09-24T00:00:00Z', 'content': '新的回應',
                   'context_source_refs': ['shared/20260901@original']}
            self.assertEqual(continuity.ingest([msg]), 1)
            self.assertEqual(continuity.ingest([msg]), 0)
            self.assertEqual(json.loads(continuity.db.execute('SELECT body FROM sources').fetchone()[0])['context_source_refs'], msg['context_source_refs'])
            continuity.close()


if __name__ == '__main__':
    unittest.main()
