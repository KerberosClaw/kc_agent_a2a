import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import test_life_context as fixtures
from test_native import packet
from test_state import registry
from discord_party.life_context import LifeContextResolver
from discord_party.life_tools import LifeQuery, LifeToolServer
from discord_party.native import Grant, NativeEngine
from discord_party.state import NotReady


class QueryCase(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LifeContextCase()
        self.fixture.setUp()
        self.root, self.policy = self.fixture.root, self.fixture.policy
        self.resolver = LifeContextResolver(self.policy, registry())
        self.query = LifeQuery(self.resolver, lambda: None)

    def tearDown(self):
        self.query.close()
        self.fixture.tearDown()

    def test_home_is_searchable_even_with_many_people_in_the_catalog(self):
        for i in range(12):
            (self.fixture.wiki / 'people' / f'朋友{i}.md').write_text(
                f'---\nname: 朋友{i}\nrelationship: 老朋友\n---\nPRIVATE_BODY\n')
        self.query = LifeQuery(self.resolver, lambda: None)
        result = self.query.call('search', {'query': '住家', 'kind': 'place'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['id'], 'home-area')
        result = self.query.call('read', {'id': 'home-area'})
        self.assertEqual(result['record']['area'], '測試市測試區')

    def test_real_aliases_find_same_record_and_fiction_alias_cannot(self):
        ids = [self.query.call('search', {'query': n})['items'][0]['id'] for n in ('林登', '小林', '小葉')]
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.query.call('search', {'query': '布丁'})['status'], 'not_available')

    def test_hidden_content_is_not_searchable_or_readable(self):
        for query in ('私密原文', '主管', '公司', '門牌', 'PRIVATE_BODY'):
            self.assertEqual(self.query.call('search', {'query': query})['total'], 0)
        records = json.dumps(self.query.records, ensure_ascii=False)
        self.assertNotIn('私密原文', records)
        self.assertNotIn('主管', records)
        self.assertNotIn(str(self.fixture.wiki), records)
        self.assertEqual(self.query.call('read', {'id': '../../people/林登.md'})['status'], 'not_available')

    def test_pages_are_explicit_and_cursor_bound_to_query(self):
        first = self.query.call('search', {'query': '', 'kind': 'person', 'page_size': 1})
        self.assertEqual(first['total'], 3)
        self.assertTrue(first['has_more'])
        ids = [first['items'][0]['id']]
        second = self.query.call('search', {'query': '', 'kind': 'person', 'page_size': 1,
                                            'cursor': first['next_cursor']})
        ids.append(second['items'][0]['id'])
        third = self.query.call('search', {'query': '', 'kind': 'person', 'page_size': 1,
                                          'cursor': second['next_cursor']})
        ids.append(third['items'][0]['id'])
        self.assertEqual(len(set(ids)), 3)
        self.assertFalse(third['has_more'])
        bad = self.query.call('search', {'query': '住家', 'cursor': first['next_cursor']})
        self.assertEqual(bad['status'], 'invalid_request')

    def test_ambiguous_alias_is_not_silently_selected(self):
        (self.fixture.wiki / 'people/另一人.md').write_text('---\nname: 另一人\naka: [阿安]\n---\n')
        q = LifeQuery(self.resolver, lambda: None)
        result = q.call('search', {'query': '阿安'})
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(result['total'], 2)
        self.assertEqual(q.call('search', {'query': '阿安是誰？'})['status'], 'ambiguous')

    def test_frozen_snapshot_and_next_turn_picks_up_source_changes(self):
        before = self.query.call('read', {'id': 'home-area'})
        p = self.fixture.wiki / 'people/安全朋友.md'
        p.write_text('---\nname: 安全朋友\naka: [阿安]\nrelationship: 鄰居\n---\n')
        q = LifeQuery(self.resolver, lambda: None)
        self.assertNotEqual(q.version, self.query.version)
        self.assertEqual(self.query.call('read', {'id': 'home-area'}), before)
        found = q.call('search', {'query': '阿安'})['items'][0]
        self.assertEqual(q.call('read', {'id': found['id']})['record']['basic_relationship'], '鄰居')

    def test_revocation_invalidates_even_a_frozen_read(self):
        self.fixture.body['status'] = 'revoked'; self.fixture.write_policy()
        self.assertEqual(self.query.call('read', {'id': 'home-area'})['status'], 'unavailable')

    def test_budget_expiry_invalid_argument_and_close_are_not_empty_results(self):
        q = LifeQuery(self.resolver, lambda: None, max_calls=1)
        self.assertEqual(q.call('search', {'query': '', 'audience': 'other'})['status'], 'invalid_request')
        self.assertEqual(q.call('read', {'id': 'home-area'})['status'], 'limited')
        q = LifeQuery(self.resolver, lambda: None, timeout=0)
        self.assertEqual(q.call('search', {'query': '住家'})['status'], 'limited')
        self.query.close()
        self.assertEqual(self.query.call('search', {'query': '住家'})['status'], 'unavailable')

    def test_tool_http_auth_protocol_allowlist_and_no_raw_errors(self):
        server = LifeToolServer(self.query).start()
        def rpc(method, params=None, token=None, **headers):
            request = Request(server.url, json.dumps({'jsonrpc': '2.0', 'id': 1,
                              'method': method, 'params': params or {}}).encode(),
                              headers={'Content-Type': 'application/json',
                                       'Authorization': 'Bearer ' + (server.token if token is None else token), **headers})
            with urlopen(request, timeout=2) as response:
                return json.load(response)
        try:
            self.assertEqual(rpc('initialize', {'protocolVersion': '2025-06-18'})['result']['protocolVersion'], '2025-06-18')
            self.assertEqual([t['name'] for t in rpc('tools/list')['result']['tools']], ['search', 'read'])
            result = rpc('tools/call', {'name': 'read', 'arguments': {'id': 'home-area'}})
            self.assertIn('測試市測試區', result['result']['content'][0]['text'])
            for headers in ({'token': 'wrong'}, {'Origin': 'https://example.com'}, {'Host': 'evil.example'}):
                with self.assertRaises(HTTPError) as error:
                    rpc('tools/list', **headers)
                self.assertEqual(error.exception.code, 403)
            self.assertEqual(rpc('resources/read')['error']['code'], -32601)
        finally:
            server.close()
        self.assertTrue(self.query.closed)
        self.assertFalse(server.thread.is_alive())


class NativeLifeCase(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_native_closes_query_and_kills_only_its_worker(self):
        fixture = fixtures.LifeContextCase(); fixture.setUp()
        try:
            root = fixture.root; packet(root / 'grant')
            engine = NativeEngine(Grant(root / 'grant', 'agent_b', registry('30')), root / 'workers',
                                  life_context=LifeContextResolver(fixture.policy, registry('30')))
            started = asyncio.Event(); saved = {}
            class Process:
                pid = 123456789
                async def communicate(self, data=None):
                    if data is None: return b'', b''
                    saved['server'] = engine.life_server
                    started.set()
                    await asyncio.Future()
            async def spawn(*args, **kwargs): return Process()
            history = [dict(message_id='100', role='human', content='我家附近',
                            created_at='2026-09-24T00:00:00Z')]
            with patch.object(engine, 'command', return_value=['fake']), \
                    patch('discord_party.native.asyncio.create_subprocess_exec', spawn), \
                    patch('discord_party.native.os.killpg') as kill:
                task = asyncio.create_task(engine.decide(history, 10))
                await asyncio.wait_for(started.wait(), 1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                kill.assert_called_once()
            self.assertIsNone(engine.life_server)
            self.assertFalse(saved['server'].thread.is_alive())
            self.assertEqual(saved['server'].query.call('read', {'id': 'home-area'})['status'], 'unavailable')
        finally:
            fixture.tearDown()

    async def test_native_current_question_queries_home_after_many_old_people(self):
        fixture = fixtures.LifeContextCase(); fixture.setUp()
        try:
            root = fixture.root
            packet(root / 'grant')
            g = Grant(root / 'grant', 'agent_b', registry('30'))
            resolver = LifeContextResolver(fixture.policy, registry('30'))
            engine = NativeEngine(g, root / 'workers', life_context=resolver)
            history = [dict(message_id=str(i), created_at='2026-09-24T00:00:00Z',
                            role='peer', content='舊朋友' + str(i)) for i in range(10)]
            history.append(dict(message_id='20', role='human', created_at='2026-09-24T01:00:00Z',
                                content='我們家附近有什麼好吃的？'))
            captured = {}
            class Process:
                returncode = 0
                async def communicate(self, data):
                    captured.update(json.loads(data))
                    q = engine.life_server.query
                    hit = q.call('search', {'query': '住家', 'kind': 'place'})['items'][0]
                    area = q.call('read', {'id': hit['id']})['record']['area']
                    final = dict(request_id=captured['request_id'], action='speak',
                                 content=area, contribution='answer')
                    events = [{'type': 'thread.started', 'thread_id': 'test'},
                              {'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'id': 'one',
                               'server': 'party_life', 'tool': 'search'}},
                              {'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'id': 'two',
                               'server': 'party_life', 'tool': 'read'}},
                              {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(final)}},
                              {'type': 'turn.completed', 'usage': {}}]
                    return '\n'.join(json.dumps(x) for x in events).encode(), b''
            async def spawn(*args, **kw): return Process()
            with patch.object(engine, 'command', return_value=['fake']), \
                    patch.object(resolver, 'resolve', side_effect=AssertionError('legacy prefetch forbidden')), \
                    patch('discord_party.native.asyncio.create_subprocess_exec', spawn):
                result = await engine.decide(history, 10)
            self.assertEqual(result, ('speak', '測試市測試區'))
            self.assertTrue(captured['life_lookup']['available'])
            self.assertNotIn('approved_life_context', captured)
            self.assertEqual(captured['messages'], history)
            self.assertEqual(engine.last_audit['life_calls'], 2)
            self.assertIsNone(engine.life_server)
        finally:
            fixture.tearDown()


class TraceToolsCase(unittest.TestCase):
    def test_native_command_keeps_os_guard_and_only_explicit_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); packet(root / 'grant')
            for agent, bot in (('agent_a', '20'), ('agent_b', '30')):
                engine = NativeEngine(Grant(root / 'grant', agent, registry(bot)), root / agent)
                engine.life_server = type('Server', (), {'url': 'http://127.0.0.1:54321/mcp'})()
                call = root / agent / 'call'; call.mkdir()
                # This test checks command construction, not installed CLI authentication.
                # Real OS denial is exercised separately by IsolationCase.
                real_is_file = Path.is_file
                with patch('discord_party.native.shutil.which', return_value='/usr/bin/true'), \
                        patch.object(Path, 'is_file', autospec=True,
                            side_effect=lambda p: str(p) == '/usr/bin/sandbox-exec' or real_is_file(p)):
                    cmd = engine.command(call, 'id')
                self.assertEqual(cmd[0], '/usr/bin/sandbox-exec')
                self.assertNotIn('Bash', cmd)
                if agent == 'agent_a':
                    self.assertIn('--restricted', cmd)
                    self.assertNotIn('--bare', cmd)
                    self.assertIn('--strict-mcp-config', cmd)
                    self.assertEqual(cmd[cmd.index('--tools')+1], 'WebSearch')
                else:
                    self.assertIn('shell_tool', cmd)
                    self.assertIn('mcp_servers.party_life.required=true', cmd)

    def test_codex_only_approved_mcp_tools_are_accepted(self):
        e = object.__new__(NativeEngine); e.engine = 'codex'; e.life_enabled = True
        events = [{'type': 'thread.started', 'thread_id': 'a'},
                  {'type': 'item.completed', 'item': {'id': 'm', 'type': 'mcp_tool_call',
                                                    'server': 'party_life', 'tool': 'search'}},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}'}},
                  {'type': 'turn.completed', 'usage': {}}]
        e.parse(events, 'unused'); self.assertEqual(e.life_calls, 1)
        events[1]['item']['tool'] = 'write'
        with self.assertRaises(NotReady): e.parse(events, 'unused')
        events[1]['item'].update(tool='search', server='other')
        with self.assertRaises(NotReady): e.parse(events, 'unused')

    def test_claude_requires_exact_connected_server_and_tools(self):
        e = object.__new__(NativeEngine); e.engine = 'claude'; e.life_enabled = True
        events = [{'type': 'system', 'subtype': 'init', 'session_id': 'a',
                   'tools': ['StructuredOutput', 'mcp__party_life__search', 'mcp__party_life__read'],
                   'mcp_servers': [{'name': 'party_life', 'status': 'connected'}]},
                  {'type': 'result', 'subtype': 'success', 'session_id': 'a', 'structured_output': {}}]
        e.parse(events, 'a')
        events[0]['mcp_servers'][0]['status'] = 'failed'
        with self.assertRaises(NotReady): e.parse(events, 'a')


if __name__ == '__main__':
    unittest.main()
