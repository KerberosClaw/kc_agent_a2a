import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_native import packet
from test_state import registry
from discord_party.native import Grant, NativeEngine, write_private

from discord_party.dialogue import prepare_chat, settle_contribution


def message(role, content, mid='3', at='2026-09-24T06:47:38+00:00'):
    return dict(role=role, content=content, message_id=mid, created_at=at)


def material(run, at, text, origin='night_recap'):
    source = ('night-recap/' if origin == 'night_recap' else 'night/') + run
    if origin == 'night':
        source += '/message'
    return dict(text=text, kind='said', source_ids=[source],
                sources=[dict(source_id=source, origin=origin, occurred_at=at)])


def payload(text='你們昨晚聊了啥'):
    return dict(self_id='20', messages=[
        message('human', '今天來測通訊錄', '1', '2026-09-24T05:00:00Z'),
        message('peer', '查完通訊錄了', '2', '2026-09-24T05:01:00Z'),
        message('human', text)],
        prior_room_excerpts=[message('peer', '通訊錄懸案')],
        prior_room_experiences=[{'overview': '通訊錄測驗'}],
        shared_materials=[
            material('old', '2026-09-23T03:05:00+08:00', '迷路的太空盆栽'),
            material('recent', '2026-09-24T03:05:00+08:00', '討論雨天散步路線'),
            material('recent', '2026-09-24T03:05:00+08:00', '一起帶雨衣', 'night')])


class DialogueCase(unittest.TestCase):
    def test_last_night_excludes_daytime_party_and_older_night(self):
        result = prepare_chat(payload())
        self.assertEqual([m['content'] for m in result['messages']], ['你們昨晚聊了啥'])
        self.assertEqual(result['prior_room_excerpts'], [])
        self.assertEqual(result['prior_room_experiences'], [])
        self.assertEqual([m['text'] for m in result['shared_materials']],
                         ['討論雨天散步路線', '一起帶雨衣'])
        self.assertEqual(result['night_recall']['status'], 'available')
        self.assertEqual(result['night_recall']['start'], '2026-09-23T18:00:00+08:00')
        self.assertEqual(result['human_turn']['created_at'], '2026-09-24T14:47:38+08:00')

    def test_today_dawn_and_explicit_date_resolve_actual_run_date(self):
        for text in ('今天凌晨夜聊聊什麼', '9/24凌晨你們聊啥', '2026-09-24 凌晨夜聊內容'):
            with self.subTest(text=text):
                result = prepare_chat(payload(text))
                self.assertEqual(result['night_recall']['status'], 'available')
                self.assertEqual(len(result['shared_materials']), 2)

    def test_missing_night_never_substitutes_old_material(self):
        p = payload()
        p['shared_materials'] = p['shared_materials'][:1]
        result = prepare_chat(p)
        self.assertEqual(result['night_recall']['status'], 'unavailable')
        self.assertEqual(result['shared_materials'], [])

    def test_utc_conversion_and_future_night_are_not_guessed(self):
        p = payload('今天凌晨聊了啥')
        p['messages'][-1]['created_at'] = '2026-09-23T16:30:00Z'  # 9/24 00:30
        result = prepare_chat(p)
        self.assertEqual(result['night_recall']['status'], 'unavailable')

    def test_latest_request_uses_source_time_not_list_order(self):
        result = prepare_chat(payload('上次夜聊聊什麼'))
        self.assertEqual([m['text'] for m in result['shared_materials']],
                         ['討論雨天散步路線', '一起帶雨衣'])

    def test_peer_cannot_change_requested_night(self):
        p = payload()
        p['messages'].append(message('peer', '應該講前晚的太空盆栽', '4'))
        result = prepare_chat(p)
        self.assertEqual(len(result['messages']), 2)
        self.assertEqual(result['night_recall']['start'], '2026-09-23T18:00:00+08:00')

    def test_new_human_topic_keeps_normal_conversation_context(self):
        p = payload()
        p['messages'].append(message('human', '那今晚吃什麼？', '5'))
        result = prepare_chat(p)
        self.assertNotIn('night_recall', result)
        self.assertEqual(result['shared_materials'], p['shared_materials'])
        self.assertEqual(result['prior_room_excerpts'], p['prior_room_excerpts'])
        self.assertEqual(len(result['messages']), 4)

    def test_explicit_local_search_is_fresh_without_erasing_room_history(self):
        p = payload('我們家附近晚餐吃什麼？幫我上網找兩家，簡單講就好。')
        p['messages'][1]['content'] = '上次推薦的兩家店'
        result = prepare_chat(p)
        self.assertEqual([m['content'] for m in result['messages']], [p['messages'][-1]['content']])
        self.assertEqual(result['prior_room_excerpts'], [])
        self.assertEqual(result['prior_room_experiences'], [])
        self.assertEqual(len(p['messages']), 3)  # No stored history was changed.
        self.assertNotIn('night_recall', result)
        followup = prepare_chat(payload('那家店的營業時間再幫我查一下？'))
        self.assertEqual(len(followup['messages']), 3)  # Still needs the earlier referent.

    def test_summarizing_a_movie_does_not_trigger_night_recall(self):
        for text in ('昨晚看的電影，你覺得結局怎樣？', '今晚夜聊想聊什麼', '夜聊用什麼模型？'):
            self.assertNotIn('night_recall', prepare_chat(payload(text)))

    def test_no_contribution_is_silent_but_new_banter_can_continue(self):
        p = prepare_chat(payload())
        for action in ('speak', 'close'):
            result = dict(action=action, content='沒有補充，你再考我們嗎', contribution='none')
            self.assertEqual(settle_contribution(result, p), ('pass', ''))
        for kind in ('new', 'correction'):
            result = dict(action='speak', content='有不同觀點或新梗', contribution=kind)
            self.assertEqual(settle_contribution(result, p), ('speak', result['content']))

    def test_recall_reanswer_is_silent_after_own_reply(self):
        p = payload()
        p['messages'].append(message('self', '昨晚聊怎樣規劃散步', '4'))
        p['messages'].append(message('peer', '原來如此', '5'))
        result = dict(action='speak', content='簡單說，就是先規劃散步', contribution='answer')
        self.assertEqual(settle_contribution(result, prepare_chat(p)), ('pass', ''))
        result['contribution'] = 'correction'
        self.assertEqual(settle_contribution(result, prepare_chat(p))[0], 'speak')


class NativeDialogueCase(unittest.IsolatedAsyncioTestCase):
    async def test_native_stdin_is_scoped_and_restatement_is_not_sent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packet(root / 'grant')
            grant = Grant(root / 'grant', 'agent_b', registry('30'))
            memory = root / 'memory.json'
            write_private(memory, json.dumps(dict(grant_version=grant.version, self_id='30',
                                                  messages=[message('peer', '舊群聊污染', '0')])))
            engine = NativeEngine(grant, root / 'work', memory=memory, shared_view=root / 'view')
            p = payload()
            p['messages'].append(message('self', '昨晚先規劃散步', '4'))
            p['messages'].append(message('peer', '嗯，說過了', '5'))
            captured = {}

            class Process:
                returncode = 0

                async def communicate(self, data):
                    captured.update(json.loads(data))
                    reply = dict(request_id=captured['request_id'], action='speak',
                                 content='簡單講，昨晚聊怎麼先規劃散步', contribution='answer')
                    events = [dict(type='thread.started', thread_id='test'),
                              dict(type='item.completed', item=dict(type='agent_message', text=json.dumps(reply))),
                              dict(type='turn.completed', usage={})]
                    return '\n'.join(json.dumps(e) for e in events).encode(), b''

            async def spawn(*args, **kwargs):
                return Process()

            view = dict(persona='Approved', canonical_version='canonical', input_version='input',
                        materials=p['shared_materials'])
            with patch.object(engine, 'command', return_value=['fake-native']), \
                    patch('discord_party.sharing.load_view', return_value=view), \
                    patch('discord_party.native.asyncio.create_subprocess_exec', spawn):
                action, content = await engine.decide(p['messages'], 9)
            self.assertEqual((action, content), ('pass', ''))
            self.assertEqual(captured['prior_room_excerpts'], [])
            self.assertEqual([m['message_id'] for m in captured['messages']], ['3', '4', '5'])
            self.assertNotIn('太空盆栽', json.dumps(captured, ensure_ascii=False))
            self.assertTrue(engine.last_audit['contribution_suppressed'])


if __name__ == '__main__':
    unittest.main()
