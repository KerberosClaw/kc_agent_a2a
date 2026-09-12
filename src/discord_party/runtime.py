"""Nonblocking event control with one bounded decision and one send at a time."""
import asyncio
import logging
import time
from typing import Protocol

from .state import Event, NotReady, State


class Engine(Protocol):
    async def decide(self, context: list[dict], remaining: int) -> tuple[str, str]: ...


class Transport(Protocol):
    async def send(self, row: dict) -> dict | None: ...


class Runtime:
    def __init__(self, state: State, engine: Engine, transport: Transport, *,
                 coalesce=3.0, interval=10.0, decision_timeout=90.0):
        self.state, self.engine, self.transport = state, engine, transport
        self.coalesce, self.interval, self.decision_timeout = coalesce, interval, decision_timeout
        self.wake = asyncio.Event()
        self.generation = None
        self.worker = None
        self.request = None

    def start(self):
        self.worker = asyncio.create_task(self.run())

    def receive(self, event: Event, *, historical=False):
        trigger = self.state.ingest(event, historical=historical)
        if self.generation and self.request and not self.state.valid_attempt(self.request):
            self.generation.cancel()
        if trigger:
            self.wake.set()

    def disconnect(self):
        self.state.disconnected()
        self.wake.clear()
        if self.generation:
            self.generation.cancel()

    async def stop(self):
        self.disconnect()
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)

    async def run(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            wait = max(self.coalesce, self.state.snapshot()['last_sent'] + self.interval - time.time())
            await asyncio.sleep(wait)
            self.request = self.state.begin()
            if not self.request:
                continue
            request = self.request
            try:
                if hasattr(self.engine, 'validate'):
                    self.engine.validate()
            except Exception:
                self.disconnect()
                logging.getLogger('discord_party').error('NATIVE_NOT_READY authorization changed')
                continue
            self.generation = asyncio.create_task(self.engine.decide(
                self.state.context(), self.state.snapshot()['remaining']))
            try:
                action, content = await asyncio.wait_for(self.generation, self.decision_timeout)
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                continue
            except Exception:  # noqa: BLE001 - boundary fails closed without logging chat or credentials
                # No original prompt/stdout/exception in diagnostics. No automatic model retry.
                self.state.finish(request, 'invalid')
                logging.getLogger('discord_party').error('DECISION_FAILED no automatic retry')
                continue
            finally:
                self.generation = None
            try:
                if hasattr(self.engine, 'validate'):
                    self.engine.validate()
            except Exception:
                self.disconnect()
                logging.getLogger('discord_party').error('NATIVE_NOT_READY authorization changed')
                continue
            if not self.state.finish(request, action, content):
                continue
            row = self.state.submit(request)
            if not row:
                continue
            try:
                receipt = await self.transport.send(row)
                if receipt is None:
                    self.state.rejected(request)
                elif not self.state.delivered(request, **receipt):
                    self.state.uncertain(request)
            except asyncio.CancelledError:
                self.state.uncertain(request)
                raise
            except NotReady:
                self.state.rejected(request)
                self.disconnect()
                logging.getLogger('discord_party').error('NOT_READY: Discord send authorization failed')
            except Exception:  # noqa: BLE001 - boundary fails closed without logging chat or credentials
                self.state.uncertain(request)


class SyntheticEngine:
    """Only a transport probe. Never loads persona data or starts a model."""
    async def decide(self, context, remaining):
        # At most two messages per human epoch; demonstrate peer receipt without a ping-pong storm.
        if remaining <= 8:
            return 'close', ''
        source = context[-1]['message_id']
        return 'speak', f'[合成傳輸測試] 已收到訊息 {source}。'
