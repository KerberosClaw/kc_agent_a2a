import unittest
from types import SimpleNamespace

from test_state import registry
from discord_party.connector import PartyClient
from discord_party.state import NotReady


class Response:
    status = 200
    def __init__(self, data):self.data = data
    async def __aenter__(self):return self
    async def __aexit__(self, *args):pass
    async def json(self):return self.data


class GateCase(unittest.IsolatedAsyncioTestCase):
    async def test_only_owner_pinned_rules_allow_native_readiness(self):
        client = object.__new__(PartyClient)
        client.engine = object()
        client.party_state = SimpleNamespace(registry=registry())
        client.party_token = 'synthetic'
        content = '先停一下 繼續聊 10 文字'
        for data in [[], [{'author': {'id': '20'}, 'content': content}],
                     [{'author': {'id': '10'}, 'content': 'hello'}]]:
            client.http_session = SimpleNamespace(get=lambda *a, **kw: Response(data))
            with self.assertRaises(NotReady):
                await client.check_room_rules()
        for data in [[{'author': {'id': '10'}, 'content': content}],
                     {'items': [{'message': {'author': {'id': '10'}, 'content': content}}]}]:
            client.http_session = SimpleNamespace(get=lambda *a, **kw: Response(data))
            await client.check_room_rules()
