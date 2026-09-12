"""Two independent connectors over a synthetic room, without Discord/model access."""
import asyncio
import tempfile
import unittest
from pathlib import Path

from test_state import event, registry

from discord_party.runtime import Runtime, SyntheticEngine
from discord_party.state import State


class PilotCase(unittest.IsolatedAsyncioTestCase):
    async def test_two_bots_peer_receipts_pause_and_replay_in_one_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            states = [State(Path(tmp)/bot,registry(bot),initialize=True) for bot in ('20','30')]
            sent, runtimes = [], []

            class Bus:
                def __init__(self, author):
                    self.author = author

                async def send(self, row):
                    mid = 101 + len(sent)
                    sent.append((mid,self.author,row['content']))
                    for runtime in runtimes:
                        runtime.receive(event(mid,self.author,row['content']))
                    return {'message_id': str(mid),'author_id': self.author,'channel_id': '2','nonce': row['nonce']}

            for state in states:
                state.synchronized()
                runtimes.append(Runtime(state,SyntheticEngine(),Bus(state.registry.self_id),coalesce=0.001,interval=0))
            for runtime in runtimes:
                runtime.start()
            try:
                for runtime in runtimes:
                    runtime.receive(event(100))
                for _ in range(100):
                    if len(sent) == 4:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual([sum(author == bot for _,author,_ in sent) for bot in ('20','30')],[2,2])
                self.assertEqual([s.snapshot()['remaining'] for s in states],[8,8])
                for runtime in runtimes:
                    runtime.receive(event(100))
                    runtime.receive(event(200,content='先停一下'))
                    runtime.receive(event(201,content='ordinary chat'))
                await asyncio.sleep(0.03)
                self.assertEqual(len(sent),4)
                self.assertTrue(all(s.snapshot()['paused'] for s in states))
                self.assertEqual([s.snapshot()['remaining'] for s in states],[10,10])
                for state in states:
                    self.assertEqual(state.db.execute('SELECT COUNT(*) FROM outbox WHERE counted=1').fetchone()[0],2)
            finally:
                for runtime in runtimes:
                    await runtime.stop()
                for state in states:
                    state.close()


if __name__ == '__main__':
    unittest.main()
