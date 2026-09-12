import asyncio
import tempfile
import unittest
from pathlib import Path

from test_state import event, registry

from discord_party.runtime import Runtime
from discord_party.state import State


class HeldEngine:
    def __init__(self):
        self.calls = []
        self.started = asyncio.Queue()
        self.answers = asyncio.Queue()
        self.cancelled = 0

    async def decide(self, context, remaining):
        self.calls.append((context, remaining))
        self.started.put_nowait(True)
        try:
            return await self.answers.get()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class HeldTransport:
    def __init__(self):
        self.calls = []
        self.started = asyncio.Queue()
        self.release = asyncio.Event()
        self.fail = False

    async def send(self, row):
        self.calls.append(row)
        self.started.put_nowait(True)
        await self.release.wait()
        if self.fail:
            raise TimeoutError('response lost')
        return {'message_id': '150', 'author_id': '20', 'channel_id': '2', 'nonce': row['nonce']}


class RuntimeCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name)/'state', registry(), initialize=True)
        self.state.synchronized()
        self.engine, self.transport = HeldEngine(), HeldTransport()
        self.runtime = Runtime(self.state, self.engine, self.transport, coalesce=0.001, interval=0)
        self.runtime.start()

    async def asyncTearDown(self):
        await self.runtime.stop()
        self.state.close()
        self.tmp.cleanup()

    async def tick(self):
        await asyncio.sleep(0.02)
        if self.runtime.worker.done():
            self.runtime.worker.result()

    async def started(self, queue):
        await asyncio.wait_for(queue.get(), timeout=1)

    async def test_stop_during_blocked_generation_is_immediate_and_cancels(self):
        self.runtime.receive(event(100))
        await self.started(self.engine.started)
        self.runtime.receive(event(200, content='先停一下'))
        self.assertTrue(self.state.snapshot()['paused'])
        await self.tick()
        self.assertEqual(self.engine.cancelled, 1)
        self.assertEqual(self.transport.calls, [])
        self.runtime.receive(event(201, content='ordinary chat'))
        await self.tick()
        self.assertEqual(len(self.engine.calls), 1)
        self.runtime.receive(event(202, content='繼續聊'))
        await self.started(self.engine.started)
        self.engine.answers.put_nowait(('speak', 'fresh'))
        await self.started(self.transport.started)
        self.transport.release.set()
        await self.tick()
        self.assertEqual(self.state.snapshot()['remaining'], 9)

    async def test_new_epoch_cancels_old_engine_then_uses_latest_context(self):
        self.runtime.receive(event(100))
        await self.started(self.engine.started)
        self.runtime.receive(event(200, content='new topic'))
        await self.started(self.engine.started)
        self.assertEqual(self.engine.cancelled, 1)
        self.assertEqual(self.engine.calls[-1][0][-1]['message_id'], '200')
        self.engine.answers.put_nowait(('pass', ''))
        await self.tick()
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(len(self.engine.calls), 2)

    async def test_stop_while_http_inflight_preserves_receipt_and_new_epoch(self):
        self.runtime.receive(event(100))
        await self.started(self.engine.started)
        self.engine.answers.put_nowait(('speak','in flight'))
        await self.started(self.transport.started)
        self.runtime.receive(event(200, content='先停一下'))
        self.assertTrue(self.state.snapshot()['paused'])
        self.transport.release.set()
        await self.tick()
        self.assertEqual(self.state.snapshot()['remaining'], 10)
        self.assertEqual(self.state.db.execute('SELECT status,counted FROM outbox').fetchone()[:], ('delivered',1))

    async def test_response_lost_stops_and_does_not_retry(self):
        self.transport.fail = True
        self.transport.release.set()
        self.runtime.receive(event(100))
        await self.started(self.engine.started)
        self.engine.answers.put_nowait(('speak','ambiguous'))
        await self.started(self.transport.started)
        await self.tick()
        self.runtime.receive(event(200))
        self.runtime.receive(event(201, '30'))
        await self.tick()
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.state.unresolved()[0]['status'], 'unknown')

    async def test_burst_coalesces_and_same_event_cannot_regenerate(self):
        self.runtime.receive(event(100))
        self.runtime.receive(event(101, '30'))
        self.runtime.receive(event(102, '30'))
        await self.started(self.engine.started)
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(len(self.engine.calls[0][0]), 3)
        self.engine.answers.put_nowait(('pass',''))
        await self.tick()
        self.runtime.receive(event(102, '30'))
        await self.tick()
        self.assertEqual(len(self.engine.calls), 1)

    async def test_engine_timeout_is_bounded_and_not_retried(self):
        self.runtime.decision_timeout = 0.01
        self.runtime.receive(event(100))
        await self.started(self.engine.started)
        await self.tick()
        self.assertEqual(self.engine.cancelled, 1)
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(self.state.snapshot()['remaining'], 10)
        self.assertEqual(self.transport.calls, [])

    async def test_disconnect_cancels_generation_and_peer_cannot_rearm(self):
        self.runtime.receive(event(100))
        await self.started(self.engine.started)
        self.runtime.disconnect()
        await self.tick()
        self.state.synchronized()
        self.runtime.receive(event(200, '30'))
        await self.tick()
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(self.transport.calls, [])


if __name__ == '__main__':
    unittest.main()
