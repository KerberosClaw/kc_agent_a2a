import json
from pathlib import Path
import tempfile
import unittest

from test_state import registry
from discord_party.life_context import LifeContextResolver, response_limit, response_mode
from discord_party.state import NotReady


class LifeContextCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.wiki = self.root / 'life_wiki'
        (self.wiki / 'people').mkdir(parents=True)
        (self.wiki / 'people/林登.md').write_text(
            '---\nname: Linden Vale（林登 / 小林）\naka: [小葉]\n'
            'met_via: 圈內飯局\nrelationship: 圈內熟人 + 飯局朋友（沒約過/沒色色）\n---\n'
            '這裡有不該出房間的私密原文。\n')
        (self.wiki / 'people/安全朋友.md').write_text(
            '---\nname: 安全朋友\naka: [阿安]\nmet_via: 大學同學\n'
            'relationship: 老朋友 + 曾任某公司主管 + 飯友\n---\n')
        (self.wiki / 'people/小說原型.md').write_text(
            '---\nname: 真實姓名\naka: [布丁（fiction 化名）]\n'
            'relationship: 普通朋友\n---\n')
        self.policy = self.root / 'policy.json'
        self.body = {
            'version': 1, 'status': 'approved', 'approved_by': '10',
            'approved_at': '2026-09-24T00:00:00+08:00',
            'intended_scope': {'guild_id': '1', 'channel_id': '2', 'human_ids': ['10']},
            'life_wiki_root': str(self.wiki),
            'defaults': {
                'allow': ['identity', 'aliases', 'basic_relationship', 'met_via',
                          'common_circle', 'coarse_location'],
                'deny': ['exact_address', 'live_location', 'health', 'intimacy',
                         'private_transcript', 'work_secret'],
            },
            'home_context': {'label': '住家生活圈', 'area': '測試市測試區'},
            'people_overrides': {
                '林登': {'aliases': ['小林', '小葉'],
                       'safe_relationship': '小禾的同學，也是房主的圈內熟人。',
                       'common_circle': '圈內飯友'},
                '小說原型': {'aliases': ['布丁']},
            },
        }
        self.write_policy()

    def tearDown(self):
        self.tmp.cleanup()

    def write_policy(self):
        self.policy.write_text(json.dumps(self.body, ensure_ascii=False))
        self.policy.chmod(0o600)

    @staticmethod
    def context(text):
        return [{'role': 'human', 'content': text}]

    def test_on_demand_override_never_exposes_page_body(self):
        cards = LifeContextResolver(self.policy, registry()).resolve(self.context('小林的 b 是誰？'))
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['name'], '林登')
        self.assertEqual(cards[0]['mentioned_as'], '小林')
        self.assertEqual(cards[0]['basic_relationship'], '小禾的同學，也是房主的圈內熟人。')
        self.assertNotIn('私密原文', json.dumps(cards, ensure_ascii=False))

    def test_global_default_filters_sensitive_relation_clause(self):
        cards = LifeContextResolver(self.policy, registry()).resolve(self.context('阿安最近如何？'))
        self.assertEqual(cards[0]['basic_relationship'], '老朋友、飯友')
        self.assertEqual(cards[0]['met_via'], '大學同學')
        self.assertNotIn('公司', json.dumps(cards, ensure_ascii=False))

    def test_parenthetical_private_detail_is_removed_as_one_unit(self):
        path = self.wiki / 'people/飯友.md'
        path.write_text('---\nname: 飯友\naka: []\nrelationship: 圈內熟人 + 飯局朋友（沒約過/沒色色）\n---\n')
        cards = LifeContextResolver(self.policy, registry()).resolve(self.context('飯友在嗎？'))
        self.assertEqual(cards[0]['basic_relationship'], '圈內熟人、飯局朋友')

    def test_relationship_story_is_reduced_to_leading_safe_label(self):
        path = self.wiki / 'people/舊識.md'
        path.write_text('---\nname: 舊識\naka: []\nrelationship: 範例市舊識、偶爾約吃飯；吃完後有發生過\n---\n')
        cards = LifeContextResolver(self.policy, registry()).resolve(self.context('舊識是誰？'))
        self.assertEqual(cards[0]['basic_relationship'], '範例市舊識')

    def test_location_only_when_relevant_and_never_exact(self):
        resolver = LifeContextResolver(self.policy, registry())
        self.assertEqual(resolver.resolve(self.context('今晚看電影')), [])
        cards = resolver.resolve(self.context('家裡附近有什麼好吃的？'))
        self.assertEqual(cards, [{'kind': 'coarse_location', 'label': '住家生活圈',
                                  'area': '測試市測試區',
                                  'precision': 'coarse; no address, coordinates or live presence'}])

    def test_fiction_alias_is_never_resolved_even_if_override_readds_it(self):
        resolver = LifeContextResolver(self.policy, registry())
        self.assertEqual(resolver.resolve(self.context('布丁是誰？')), [])
        cards = resolver.resolve(self.context('真實姓名是誰？'))
        self.assertEqual(cards[0]['name'], '小說原型')

    def test_policy_is_bound_to_audience_and_pinned(self):
        bad = dict(self.body, intended_scope={'guild_id': '1', 'channel_id': '2',
                                               'human_ids': ['10', '11']})
        self.policy.write_text(json.dumps(bad)); self.policy.chmod(0o600)
        with self.assertRaises(NotReady):
            LifeContextResolver(self.policy, registry())
        self.write_policy()
        resolver = LifeContextResolver(self.policy, registry())
        self.body['home_context']['area'] = '另一區'
        self.write_policy()
        with self.assertRaises(NotReady):
            resolver.resolve(self.context('家裡附近'))

    def test_response_modes_and_hard_limits(self):
        self.assertEqual(response_mode(self.context('笑死')), 'casual')
        self.assertEqual(response_mode(self.context('附近有什麼餐廳？')), 'informational')
        self.assertEqual(response_mode(self.context('幫我上網查一下，詳細說')), 'deep')
        self.assertEqual([response_limit(x) for x in ('casual', 'informational', 'deep')],
                         [80, 200, 500])


if __name__ == '__main__':
    unittest.main()
