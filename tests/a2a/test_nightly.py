import json
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from a2a.digest_hook import handle
from a2a.digests import recent
from a2a.nightly import tick,eligible
from a2a.storage import BoundaryError,Store
from a2a.coordinator import Coordinator


class NightlyTests(unittest.TestCase):
    def fixture(self,root):
        run=str(uuid.uuid4());mid=str(uuid.uuid4());p=root/'content/runs'/run;p.mkdir(parents=True)
        (p/'snapshot.json').write_text(json.dumps({'run_id':run,'started_at':'2026-09-08T03:01:00+08:00','night_label':'2026-09-07','mode':'nightly','stop_reason':'closure',
          'messages':[{'id':mid}], 'digests':{'agent_a':{'author':'agent_a','summary':'fixture relationship','shared_message_ids':[mid]},'agent_b':None}}))
        return run

    def test_hook_requires_registered_identity_and_completed_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sid=str(uuid.uuid4());run=self.fixture(root)
            config={'runtime':str(root/'runtime'),'content':str(root/'content'),'sessions':{sid:{'cwd':str(root),'agent':'agent_a'}}}
            event={'session_id':sid,'cwd':str(root),'hook_event_name':'UserPromptSubmit','turn_id':'turn-a','prompt':'hi'}
            self.assertIsNone(handle(config,dict(event,session_id=str(uuid.uuid4()))))
            self.assertIsNone(handle(config,dict(event,cwd=str(root/'other'))))
            self.assertIsNone(handle(config,dict(event,agent_id='child')))
            self.assertIn(run,handle(config,event)['hookSpecificOutput']['additionalContext'])
            handle(config,dict(event,hook_event_name='Stop',turn_id='foreign',last_assistant_message='done'))
            self.assertIsNotNone(handle(config,event))
            handle(config,dict(event,hook_event_name='Stop',last_assistant_message='done'))
            self.assertIsNone(handle(config,event))
            self.assertIsNotNone(handle(config,dict(event,hook_event_name='SessionStart',source='compact')))

    def test_digest_rejects_foreign_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=self.fixture(root);p=root/'content/runs'/run/'snapshot.json';s=json.loads(p.read_text());s['digests']['agent_a']['shared_message_ids']=['not-shared'];p.write_text(json.dumps(s))
            with self.assertRaises(BoundaryError):recent(root/'content')

    def test_nightly_does_not_call_models_outside_window_or_after_daily_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data={'runtime':root,'content':root/'content'}
            (root/'nightly-control.json').write_text('{"enabled":true}')
            now=datetime(2026,9,8,2,tzinfo=ZoneInfo('Asia/Taipei'))
            with patch('a2a.nightly.quiet_hours',return_value=(3,6)),patch('a2a.nightly.run_trial') as run:
                self.assertEqual(tick(data,now)['status'],'outside_window');run.assert_not_called()
            s=Store(root/'state');Coordinator(s).start('nightly:2026-09-08','2026-09-08');s.close()
            with patch('a2a.nightly.quiet_hours',return_value=(3,6)),patch('a2a.nightly.run_trial') as run:
                self.assertEqual(tick(data,now.replace(hour=3))['status'],'window_used');run.assert_not_called()
            self.assertTrue(eligible(now.replace(hour=23),(22,6)))
            self.assertFalse(eligible(now.replace(hour=12),(22,6)))
