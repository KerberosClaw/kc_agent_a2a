import tempfile
import unittest
from pathlib import Path

from test_state import event, registry

from discord_party.installation import bind_bot, outside_repository, read_status
from discord_party.state import NotReady, State


class InstallationCase(unittest.TestCase):
    def test_binding_prevents_duplicate_bot_and_reinitialize_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with bind_bot(root/'bindings',registry(),root/'state',initialize=True):  # noqa: SIM117 - initial acquisition must succeed outside assertRaises
                with self.assertRaises(BlockingIOError), bind_bot(root/'bindings',registry(),root/'state'):
                    pass
            with self.assertRaises(FileExistsError), bind_bot(root/'bindings',registry(),root/'fresh',initialize=True):
                pass
            with self.assertRaises(NotReady), bind_bot(root/'bindings',registry(),root/'fresh'):
                pass
            with bind_bot(root/'bindings',registry(),root/'state'):
                pass

    def test_status_is_read_only_even_with_live_instance_and_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'state'
            state = State(root,registry(),initialize=True)
            try:
                state.synchronized()
                state.ingest(event(100))
                request = state.begin()
                before = state.snapshot()
                self.assertEqual(read_status(root,registry()),before)
                self.assertEqual(state.snapshot(),before)
                self.assertTrue(state.finish(request,'speak','still valid'))
            finally:
                state.close()

    def test_runtime_and_tokens_cannot_be_inside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'.git').mkdir()
            with self.assertRaises(NotReady):
                outside_repository(root/'runtime/state')
            with self.assertRaises(NotReady):
                outside_repository(root/'token')


if __name__ == '__main__':
    unittest.main()
