import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_state import registry, event
from test_runtime import HeldEngine, HeldTransport
from discord_party.native import Grant, NativeEngine, sandbox_profile, write_private
from discord_party.runtime import Runtime
from discord_party.state import NotReady, State


def packet(root):
    root.mkdir()
    files = {'agent_a.md': 'Synthetic persona A', 'agent_b.md': 'Synthetic persona B',
             'room_boundary.md': 'Only this room.'}
    manifest = {'status': 'approved', 'approved_at': '2026-09-09T00:00:00Z', 'approved_by': '10',
                'agent_bot_ids': {'agent_a': '20', 'agent_b': '30'},
                'intended_scope': {'guild_id': '1', 'channel_id': '2', 'human_ids': ['10'],
                                   'bot_ids': ['20', '30']},
                'profile_files': [{'name': k, 'sha256': hashlib.sha256(v.encode()).hexdigest()}
                                  for k, v in files.items()]}
    for name, value in files.items():
        write_private(root / name, value)
    write_private(root / 'manifest.json', json.dumps(manifest))
    return manifest


class GrantCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.manifest = packet(self.root / 'grant')

    def tearDown(self):
        self.tmp.cleanup()

    def test_background_grants_keep_models_in_approved_manifest(self):
        from discord_party.sharing import CuratorGrant
        from discord_party.shared_context import ContextGrant
        from types import SimpleNamespace
        from unittest.mock import patch
        self.manifest['native_models'] = {'agent_a': 'fixture-claude', 'agent_b': 'fixture-codex'}
        write_private(self.root / 'grant/manifest.json', json.dumps(self.manifest))
        for agent, bot in [('agent_a', '20'), ('agent_b', '30')]:
            original = Grant(self.root / 'grant', agent, registry(bot))
            with patch('discord_party.sharing.canonical', return_value={'version': 'fixture'}):
                wrappers = [CuratorGrant(original, self.root / 'canonical-without-manifest'),
                            ContextGrant(original, SimpleNamespace())]
            for wrapper in wrappers:
                engine = NativeEngine(wrapper, self.root / 'worker')
                self.assertEqual(engine.model, self.manifest['native_models'][agent])

    def test_exact_approved_packet_and_agent_binding(self):
        g = Grant(self.root / 'grant', 'agent_a', registry())
        self.assertEqual(g.persona, 'Synthetic persona A')
        with self.assertRaises(NotReady):
            Grant(self.root / 'grant', 'agent_a', registry('30'))

    def test_content_tampering_and_revocation_fail_closed(self):
        g = Grant(self.root / 'grant', 'agent_a', registry())
        write_private(self.root / 'grant/agent_a.md', 'new unapproved private content')
        with self.assertRaises(NotReady):
            g.validate()
        write_private(self.root / 'grant/agent_a.md', 'Synthetic persona A')
        self.manifest['status'] = 'revoked'
        write_private(self.root / 'grant/manifest.json', json.dumps(self.manifest))
        with self.assertRaises(NotReady):
            g.validate()

    def test_audience_expansion_requires_new_approval(self):
        g = Grant(self.root / 'grant', 'agent_a', registry())
        self.manifest['intended_scope']['human_ids'].append('40')
        write_private(self.root / 'grant/manifest.json', json.dumps(self.manifest))
        with self.assertRaises(NotReady):
            g.validate()

    def test_reapproval_excludes_previously_shared_context(self):
        g = Grant(self.root / 'grant', 'agent_a', registry())
        old = {'message_id': '1', 'created_at': '2026-09-08T23:59:59Z', 'content': 'withdrawn data'}
        new = {'message_id': '2', 'created_at': '2026-09-09T00:00:01Z', 'content': 'new public data'}
        self.assertEqual(g.filter_context([old, new]), [new])

    def test_symlink_and_public_permissions_rejected(self):
        p = self.root / 'grant/agent_a.md'
        p.chmod(0o644)
        with self.assertRaises(NotReady):
            Grant(self.root / 'grant', 'agent_a', registry())

        p.unlink()
        p.symlink_to(self.root / 'grant/agent_b.md')
        with self.assertRaises(NotReady):
            Grant(self.root / 'grant', 'agent_a', registry())

    def test_explicit_style_revision_retains_only_previously_shared_window(self):
        self.manifest.update(approved_at='2026-09-10T00:00:00Z',
                             history_since='2026-09-09T00:00:00Z',
                             revision_kind='style_only', supersedes='previous-approved-version')
        write_private(self.root / 'grant/manifest.json', json.dumps(self.manifest))
        g = Grant(self.root / 'grant', 'agent_a', registry())
        old = {'created_at': '2026-09-08T23:59:59Z'}
        shared = {'created_at': '2026-09-09T12:00:00Z'}
        self.assertEqual(g.filter_context([old, shared]), [shared])
        del self.manifest['revision_kind']
        write_private(self.root / 'grant/manifest.json', json.dumps(self.manifest))
        with self.assertRaises(NotReady):
            Grant(self.root / 'grant', 'agent_a', registry())

    def test_new_grant_preserves_quota_and_requires_live_human(self):
        s = State(self.root / 'state', registry(), initialize=True)
        try:
            s.synchronized(); s.ingest(event(100)); request = s.begin()
            s.set_grant('approved-v1')
            self.assertFalse(s.valid_attempt(request))
            self.assertEqual(s.snapshot()['remaining'], 10)
            self.assertFalse(s.snapshot()['armed'])
            s.ingest(event(101))
            self.assertIsNotNone(s.begin())
        finally:
            s.close()


class TraceCase(unittest.TestCase):
    def engine(self, kind):
        e = object.__new__(NativeEngine);e.engine = kind
        return e

    def test_codex_requires_completion_and_rejects_tools(self):
        e = self.engine('codex')
        events = [{'type': 'thread.started', 'thread_id': 'a'},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}'}},
                  {'type': 'turn.completed', 'usage': {}}]
        self.assertEqual(e.parse(events, 'ignored')[1], 'a')
        with self.assertRaises(NotReady):
            e.parse(events[:-1], 'ignored')
        events.insert(1, {'type': 'item.started', 'item': {'type': 'command_execution'}})
        with self.assertRaises(NotReady):
            e.parse(events, 'ignored')

    def test_claude_rejects_extra_capability_and_cross_session(self):
        e = self.engine('claude')
        events = [{'type': 'system', 'subtype': 'init', 'session_id': 'a', 'tools': ['StructuredOutput'], 'mcp_servers': []},
                  {'type': 'result', 'subtype': 'success', 'session_id': 'a', 'structured_output': {}}]
        self.assertEqual(e.parse(events, 'a')[1], 'a')
        with self.assertRaises(NotReady):
            e.parse(events, 'b')
        events[0]['tools'].append('Bash')
        with self.assertRaises(NotReady):
            e.parse(events, 'a')

    def test_claude_allows_search_but_not_fetch_files_or_commands(self):
        e = self.engine('claude')
        events = [{'type': 'system', 'subtype': 'init', 'session_id': 'a',
                   'tools': ['StructuredOutput', 'WebSearch'], 'mcp_servers': []},
                  {'type': 'assistant', 'message': {'content': [
                      {'type': 'tool_use', 'name': 'WebSearch', 'id': 'search-1'}]}},
                  {'type': 'result', 'subtype': 'success', 'session_id': 'a', 'structured_output': {}}]
        e.parse(events, 'a')
        self.assertEqual(e.web_calls, 1)
        for tool in ('WebFetch', 'Read', 'Bash', 'Write', 'mcp__browser'):
            with self.subTest(tool=tool):
                events[1]['message']['content'][0]['name'] = tool
                with self.assertRaises(NotReady):
                    e.parse(events, 'a')

    def test_codex_search_lifecycle_counts_once_and_other_tools_rejected(self):
        e = self.engine('codex')
        events = [{'type': 'thread.started', 'thread_id': 'a'},
                  {'type': 'item.started', 'item': {'id': 'web-1', 'type': 'web_search'}},
                  {'type': 'item.completed', 'item': {'id': 'web-1', 'type': 'web_search'}},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}'}},
                  {'type': 'turn.completed', 'usage': {}}]
        e.parse(events, 'ignored')
        self.assertEqual(e.web_calls, 1)
        for tool in ('command_execution', 'file_change', 'mcp_tool_call', 'collab_tool_call'):
            with self.subTest(tool=tool):
                events[1]['item']['type'] = tool
                with self.assertRaises(NotReady):
                    e.parse(events, 'ignored')

    def test_codex_search_budget_ignores_non_query_bookkeeping(self):
        e = self.engine('codex')
        events = [{'type': 'thread.started', 'thread_id': 'a'}]
        events += [{'type': 'item.completed', 'item': {'id': 'web-' + str(n), 'type': 'web_search',
                   'action': {'type': 'search', 'queries': ['q']}}} for n in range(3)]
        events += [{'type': 'item.completed', 'item': {'id': 'bookkeeping', 'type': 'web_search',
                    'action': {'type': 'other'}}},
                   {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}'}},
                   {'type': 'turn.completed', 'usage': {}}]
        e.parse(events, 'ignored')
        self.assertEqual(e.web_calls, 3)
        events.insert(-2, {'type': 'item.completed', 'item': {'id': 'web-4', 'type': 'web_search',
                       'action': {'type': 'search', 'queries': ['q']}}})
        with self.assertRaises(NotReady):
            e.parse(events, 'ignored')


@unittest.skipUnless(Path('/usr/bin/sandbox-exec').exists(), 'macOS OS guard')
class IsolationCase(unittest.TestCase):
    def test_actual_private_reads_and_symlink_escape_denied(self):
        # Exercise actual OS access, not just string matching a sandbox profile.
        with tempfile.TemporaryDirectory(dir=Path.home(), prefix='.party-isolation-') as temp:
            root = Path(temp);work = root / 'worker';work.mkdir()
            private = root / 'forbidden.txt';private.write_text('PRIVATE_MARKER_ONLY_ON_DISK')
            allowed = work / 'public.txt';allowed.write_text('public')
            (work / 'escape').symlink_to(private)
            for engine in ('claude', 'codex'):
                profile = sandbox_profile(work, engine, '/bin/cat')
                def read(path):
                    return subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, '/bin/cat', str(path)],
                                          capture_output=True, text=True)
                self.assertEqual(read(allowed).stdout, 'public')
                for path in (private, work / 'escape'):
                    result = read(path)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn('PRIVATE_MARKER_ONLY_ON_DISK', result.stdout)
                result = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, '/usr/bin/touch', str(private)],
                                        capture_output=True)
                self.assertNotEqual(result.returncode, 0)


class RevocationCase(unittest.IsolatedAsyncioTestCase):
    async def test_revoke_during_generation_never_sends(self):
        with tempfile.TemporaryDirectory() as temp:
            s = State(Path(temp) / 'state', registry(), initialize=True)
            s.synchronized(); engine = HeldEngine(); transport = HeldTransport()
            allowed = True
            def validate():
                if not allowed:
                    raise NotReady('revoked')
            engine.validate = validate
            runtime = Runtime(s, engine, transport, coalesce=0.001, interval=0)
            runtime.start()
            try:
                runtime.receive(event(100))
                await asyncio.wait_for(engine.started.get(), 1)
                allowed = False
                engine.answers.put_nowait(('speak', 'late reply'))
                await asyncio.sleep(0.03)
                self.assertEqual(transport.calls, [])
                self.assertFalse(s.snapshot()['ready'])
            finally:
                await runtime.stop();s.close()
