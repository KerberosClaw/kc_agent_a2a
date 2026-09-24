import json
from pathlib import Path
import tempfile
import time
import unittest

from test_native import packet
from test_state import registry
from discord_party.continuity import encode, fingerprint
from discord_party.continuity_save import canonical
from discord_party.native import Grant, write_private
from discord_party.sharing import COMMON_RECAP_POLICY, attach_common_recap, refresh, load_view
from discord_party.state import NotReady


class SharingCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        (self.root / 'persona').mkdir(); (self.root / 'persona/patches').mkdir()
        (self.root / 'persona/agent_a_testament.md').write_text('Synthetic private canonical personality')
        self.scope = {'approved_by': '10', 'human_ids': ['10'], 'mode': 'style_and_nonprivate_material'}
        manifest = packet(self.root / 'grant'); manifest['shared_view_policy'] = fingerprint(self.scope)
        write_private(self.root / 'grant/manifest.json', encode(manifest))
        self.grant = Grant(self.root / 'grant', 'agent_a', registry())
        self.config = {'party_runtime': str(self.root), 'interactive_roots': {'agent_a': str(self.root / 'persona')}, 'sharing_scope': self.scope}
        self.sources = [{'source_id': 'night/run/1', 'origin': 'night', 'occurred_at': '2026-09-12T00:00:00Z',
                         'expires_at': 200, 'body': {'author': 'agent_a', 'text': 'hypothetical'}}]
        self.calls = []
        outer = self
        class Fake:
            def __init__(self, grant, work, **kwargs):
                outer.calls.append(kwargs)
                self.material = kwargs['instructions'].startswith('你在獨立 session')
                self.last_audit = {'ephemeral': True}
            async def decide(self, messages, remaining):
                payload = json.loads(messages[0]['content'])
                if 'candidate' in payload:
                    return 'speak', encode({'persona_approved': True, 'approved_material_indices': [0], 'issues': []})
                if self.material:
                    return 'speak', encode({'materials': [{'source_ids': ['night/run/1'], 'kind': 'hypothetical', 'text': 'Agent A imagined a scene.'}]})
                outer.assertEqual(payload['sources'], [])
                return 'speak', encode({'persona': 'Synthetic natural narrative. ' * 60, 'materials': []})
        self.factory = Fake

    def tearDown(self):
        self.tmp.cleanup()

    async def test_richer_derived_view_and_expiration(self):
        result = await refresh(self.config, self.grant, self.sources, engine_factory=self.factory, now=100)
        self.assertEqual(result['status'], 'published')
        view = load_view(self.root / 'sharing/agent_a', self.grant, now=150)
        self.assertGreater(len(view['persona']), 1000)
        self.assertEqual(view['canonical_version'], canonical(self.root / 'persona', 'agent_a')['version'])
        self.assertEqual(len(view['materials']), 1)
        self.assertEqual(load_view(self.root / 'sharing/agent_a', self.grant, now=201)['materials'], [])
        await refresh(self.config, self.grant, self.sources, engine_factory=self.factory, now=101)
        self.assertEqual(len(self.calls), 3)  # unchanged inputs start no model
        self.assertTrue(all(not c['allow_web'] for c in self.calls))

    def test_latest_common_recap_is_identical_and_replaces_selector_variant(self):
        self.assertEqual(COMMON_RECAP_POLICY, 'latest_identical_candidate_v3')
        sources = [
            {'source_id': 'night-recap/old', 'origin': 'night_recap',
             'occurred_at': '2026-09-11T03:00:00+08:00',
             'body': {'started_at': '2026-09-11T03:00:00+08:00', 'summary': '舊回顧'}},
            {'source_id': 'night-recap/new', 'origin': 'night_recap',
             'occurred_at': '2026-09-12T03:00:00+08:00',
             'body': {'started_at': '2026-09-12T03:00:00+08:00', 'summary': '共同內容'}},
        ]
        chosen = [{'source_ids': ['night-recap/new'], 'kind': 'said', 'text': 'persona-specific paraphrase'}]
        result = attach_common_recap(chosen, sources)
        self.assertEqual(result, [{'source_ids': ['night-recap/new'], 'kind': 'said',
                                   'text': '夜聊共同回顧（2026-09-12T03:00:00+08:00）：共同內容'}])

    def test_invalid_selector_material_does_not_block_auditing_the_common_recap(self):
        sources = [{'source_id': 'night-recap/new', 'origin': 'night_recap',
                    'occurred_at': '2026-09-12T03:00:00+08:00',
                    'body': {'started_at': '2026-09-12T03:00:00+08:00', 'summary': '共同內容'}}]
        invalid = [{'source_ids': ['missing', 'missing'], 'kind': 'joke', 'text': 'bad provenance'}]
        self.assertEqual(attach_common_recap(invalid, sources)[0]['source_ids'], ['night-recap/new'])

    async def test_new_patch_triggers_refresh_preserves_old_version(self):
        one = await refresh(self.config, self.grant, self.sources, engine_factory=self.factory, now=100)
        (self.root / 'persona/patches/20260912_session1.md').write_text('An abstract style change')
        two = await refresh(self.config, self.grant, self.sources, engine_factory=self.factory, now=101)
        self.assertNotEqual(one['canonical_version'], two['canonical_version'])
        self.assertTrue((self.root / 'sharing/agent_a/versions' / (one['view_version'] + '.json')).exists())
        self.assertEqual(len(self.calls), 6)

    async def test_audit_failure_never_replaces_good_view(self):
        one = await refresh(self.config, self.grant, self.sources, engine_factory=self.factory, now=100)
        original = self.factory
        class Deny(original):
            async def decide(self, messages, remaining):
                if 'candidate' in json.loads(messages[0]['content']):
                    return 'speak', encode({'persona_approved': False, 'approved_material_indices': [],
                                           'issues': [{'field': 'persona', 'reason': 'privacy', 'detail': 'new private fact'}]})
                return await super().decide(messages, remaining)
        (self.root / 'persona/patches/20260912_session1.md').write_text('Private new event')
        two = await refresh(self.config, self.grant, self.sources, engine_factory=Deny, now=101)
        self.assertEqual(two['status'], 'held_for_review')
        self.assertEqual(json.loads((self.root / 'sharing/agent_a/current.json').read_text())['version'], one['view_version'])

    async def test_material_refresh_keeps_identical_previously_approved_persona(self):
        await refresh(self.config, self.grant, self.sources, engine_factory=self.factory, now=100)
        original = self.factory
        class PersonaFalse(original):
            async def decide(self, messages, remaining):
                if 'candidate' in json.loads(messages[0]['content']):
                    return 'speak', encode({'persona_approved': False, 'approved_material_indices': [0],
                                            'issues': [{'field': 'persona', 'reason': 'privacy',
                                                        'detail': 'nondeterministic repeat finding'}]})
                return await super().decide(messages, remaining)
        changed = [dict(self.sources[0], body={'author': 'agent_a', 'text': 'changed hypothetical'})]
        two = await refresh(self.config, self.grant, changed, engine_factory=PersonaFalse, now=101)
        self.assertEqual(two['status'], 'published')

    async def test_source_change_during_audit_discards_output(self):
        original = self.factory; root = self.root
        class Racing(original):
            async def decide(self, messages, remaining):
                result = await super().decide(messages, remaining)
                if 'candidate' in json.loads(messages[0]['content']):
                    (root / 'persona/patches/new.md').write_text('changed during review')
                return result
        with self.assertRaises(NotReady):
            await refresh(self.config, self.grant, self.sources, engine_factory=Racing, now=100)
        self.assertFalse((self.root / 'sharing/agent_a/current.json').exists())

    async def test_audience_scope_and_file_tampering_fail_closed(self):
        with self.assertRaises(NotReady):
            await refresh(dict(self.config, sharing_scope=dict(self.scope, human_ids=['10', '40'])), self.grant, self.sources, engine_factory=self.factory, now=100)
        result = await refresh(self.config, self.grant, self.sources, engine_factory=self.factory, now=100)
        path = self.root / 'sharing/agent_a/versions' / (result['view_version'] + '.json')
        data = json.loads(path.read_text()); data['persona'] += 'unapproved change'
        write_private(path, encode(data))
        with self.assertRaises(NotReady):
            load_view(self.root / 'sharing/agent_a', self.grant)


if __name__ == '__main__':
    unittest.main()
