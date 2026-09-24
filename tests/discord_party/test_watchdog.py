import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('party_watchdog',
    Path(__file__).resolve().parents[2] / 'scripts/discord_party/watchdog.py')
watchdog = importlib.util.module_from_spec(spec); spec.loader.exec_module(watchdog)

def report(*, agent_a=True, agent_b=True, archive=True, ready=True):
    return {'healthy':agent_a and agent_b and archive and ready,
        'processes':{'agent_a':agent_a,'agent_b':agent_b,'archive':archive},
        'bots':{'agent_a':{'ready':int(ready)},'agent_b':{'ready':int(ready)}}, 'release':'/release'}

class WatchdogCase(unittest.TestCase):
    def test_partial_failure_notifies_once_and_never_restarts(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(watchdog,'health',return_value=report(agent_b=False)), \
             patch.object(watchdog,'manager_start') as start, patch.object(watchdog,'notify',return_value='123') as notify:
            one=watchdog.tick(Path(temp),'/notify','agent-a2a',current_boot='boot-a')
            two=watchdog.tick(Path(temp),'/notify','agent-a2a',current_boot='boot-a')
            self.assertFalse(one['healthy']); self.assertTrue(one['notified']); self.assertFalse(two['healthy'])
            start.assert_not_called(); self.assertEqual(notify.call_count,1)

    def test_new_boot_all_dead_starts_once_and_confirms_ready(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(watchdog,'health',side_effect=[report(agent_a=False,agent_b=False,archive=False),report()]), \
             patch.object(watchdog,'manager_start') as start, patch.object(watchdog.time,'sleep'):
            result=watchdog.tick(Path(temp),'/notify','agent-a2a',current_boot='boot-b')
            self.assertTrue(result['healthy']); self.assertEqual(result['action'],'boot_start_attempted'); start.assert_called_once()

    def test_same_boot_all_dead_alerts_without_restart(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(watchdog,'health',return_value=report(agent_a=False,agent_b=False,archive=False)), \
             patch.object(watchdog,'manager_start') as start, patch.object(watchdog,'notify',return_value='123'):
            watchdog.atomic_json(Path(temp)/'watchdog/state.json',{'boot_id':'boot-c'})
            result=watchdog.tick(Path(temp),'/notify','agent-a2a',current_boot='boot-c')
            self.assertFalse(result['healthy']); start.assert_not_called()

    def test_notification_requires_confirmed_message_id(self):
        with patch.object(watchdog,'run_json',return_value={'accepted':True}):
            with self.assertRaises(RuntimeError):
                watchdog.notify('/notify','source','failed','message','incident','event')

    def test_notifier_contract_preserves_idempotency_key(self):
        with patch.object(watchdog, 'run_json', return_value={'delivered': True, 'message_id': 'receipt'}) as run:
            self.assertEqual(watchdog.notify('/notify', 'source', 'failed', 'message', 'incident', 'stable-id'), 'receipt')
            self.assertEqual(run.call_args.kwargs['payload']['event_id'], 'stable-id')
            self.assertEqual(run.call_args.args[0], ['/notify'])
        with patch.object(watchdog, 'run_json', return_value={'delivered': False, 'message_id': 'queued'}):
            with self.assertRaises(RuntimeError):
                watchdog.notify('/notify', 'source', 'failed', 'message', 'incident', 'stable-id')

class InstallCase(unittest.TestCase):
    def test_direct_checkout_and_installation_isolation(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)), patch.object(watchdog.subprocess, 'run'):
            runtime = Path(temp) / 'runtime'; runtime.mkdir()
            result = watchdog.install(runtime, '/notify', 'agent-a2a')
            import plistlib
            data = plistlib.loads(Path(result['plist']).read_bytes())
            self.assertEqual(data['ProgramArguments'][0], watchdog.sys.executable)
            self.assertTrue(Path(data['ProgramArguments'][1]).is_file())
            self.assertNotEqual(watchdog.watchdog_label(runtime), watchdog.watchdog_label(runtime / 'another'))

    def test_plist_uses_current_release_and_five_minute_interval(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path,'home',return_value=Path(temp)), patch.object(watchdog.subprocess,'run'):
            runtime=Path(temp)/'runtime'; runtime.mkdir()
            release=runtime/'releases/v1'; release.mkdir(parents=True)
            (runtime/'current').symlink_to(release)
            result=watchdog.install(runtime,'/notify','agent-a2a')
            import plistlib
            data=plistlib.loads(Path(result['plist']).read_bytes())
            self.assertEqual(data['StartInterval'],300)
            self.assertIn('current/scripts/discord_party/watchdog.py',data['ProgramArguments'][1])
            self.assertNotIn('.codex/tmp', data['EnvironmentVariables']['PATH'])

if __name__ == '__main__': unittest.main()
