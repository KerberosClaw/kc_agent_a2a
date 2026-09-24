import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from a2a.material import collect_material
from a2a.storage import BoundaryError
from a2a.trial import configuration
from a2a.nightly import quiet_hours
from discord_party.continuity_save import canonical
from discord_party.native import Grant
from discord_party.state import Registry

script=Path(__file__).resolve().parents[2]/'scripts/setup_preview.py'
spec=importlib.util.spec_from_file_location('setup_preview',script)
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)


class PublicSetupTests(unittest.TestCase):
    def test_no_private_dependency_and_approval_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'new';setup.prepare(root)
            data=configuration(root/'runtime/config.json')
            self.assertEqual(set(collect_material(data)),{'agent_a','agent_b'})
            self.assertIn('baseline.md',canonical(root/'personas/agent_a','agent_a')['files'])
            self.assertEqual(quiet_hours(root,data),(3,6))
            with self.assertRaises(ValueError):setup.prepare(root)
            setup.room(root,'1','2',['10'],['20','30'])
            raw=json.loads((root/'party/config/agent_a.json').read_text());raw['human_ids']=tuple(raw['human_ids'])
            with self.assertRaises(Exception):Grant(root/'party/review/initial','agent_a',Registry(**raw))
            setup.approve(root)
            grant=Grant(root/'party/review/current','agent_a',Registry(**raw))
            setup.approve_sharing(root)
            with self.assertRaises(Exception):grant.validate()
            self.assertIn('sharing_scope',json.loads((root/'continuity-config.json').read_text()))
            self.assertFalse((root/'social').exists())

    def test_material_is_opt_in_and_missing_or_oversized_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=root/'source.md';p.write_text('A synthetic note')
            data={'personas':{'agent_a':{},'agent_b':{}}}
            self.assertEqual(collect_material(data)['agent_a']['source_status'],'EXPLICITLY_UNCONFIGURED')
            data['material_files']=[{'id':'note','path':str(p)}]
            self.assertEqual(collect_material(data)['agent_a']['shared_user_records'][0]['text'],'A synthetic note')
            p.write_text('x'*20001)
            with self.assertRaises(BoundaryError):collect_material(data)
            p.unlink()
            with self.assertRaises(BoundaryError):collect_material(data)

    def test_canonical_manifest_cannot_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'persona.json').write_text('{"baseline":"../outside.md"}')
            with self.assertRaises(Exception):canonical(root,'agent_a')

    def test_context_example_is_opt_in_and_matches_fresh_install(self):
        from discord_party.life_context import LifeContextResolver
        from discord_party.shared_context import settings
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'installation'; setup.prepare(root)
            setup.room(root, '1', '2', ['10'], ['20', '30']); setup.approve(root)
            self.assertFalse((root / 'party/config/life-context.json').exists())
            knowledge = root / 'knowledge'; (knowledge / 'people').mkdir(parents=True)
            (knowledge / 'raw/entries').mkdir(parents=True)
            (knowledge / 'people/Linden.md').write_text('---\nname: Linden\naka: [Lin]\nrelationship: 同學\n---\nPRIVATE_BODY\n')
            example = script.parent / 'discord_party/life-context.example.json'
            policy = json.loads(example.read_text())
            policy.update(approved_by='10', life_wiki_root=str(knowledge),
                          intended_scope={'guild_id': '1', 'channel_id': '2', 'human_ids': ['10']})
            policy['shared_context'].update(root=str(root / 'party/shared-context'),
                journal_roots={a: str(root / 'personas' / a) for a in ('agent_a', 'agent_b')},
                agent_bot_ids={'agent_a': '20', 'agent_b': '30'})
            path = root / 'party/config/life-context.json'; path.write_text(json.dumps(policy)); path.chmod(0o600)
            raw = json.loads((root / 'party/config/agent_a.json').read_text()); raw['human_ids'] = tuple(raw['human_ids'])
            reg = Registry(**raw)
            with self.assertRaises(Exception): LifeContextResolver(path, reg)
            policy.update(status='approved', approved_at='2026-09-01T00:00:00Z')
            path.write_text(json.dumps(policy))
            resolver = LifeContextResolver(path, reg)
            self.assertEqual(settings(resolver)[2], root / 'party/shared-context')
            self.assertNotIn('PRIVATE_BODY', json.dumps(resolver.approved_records()))
            self.assertTrue(any('Lin' in row.get('aliases', []) for row in resolver.approved_records()))
