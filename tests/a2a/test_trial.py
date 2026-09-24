import json
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from a2a.codex_adapter import CodexAdapter
from a2a.storage import BoundaryError, NativeRefusal, Store
from a2a.coordinator import Coordinator, LIMITS
from a2a.trial import run_trial, configuration


class _RefusingAdapter:
    """Refuses agent_a's opening prepare a fixed number of times, then behaves normally."""

    def __init__(self,*args,refuse=1,seen=None,**kwargs):
        self.audit=[];self.refuse=refuse;self.seen=seen if seen is not None else []

    def call(self,request,timeout):
        self.seen.append(request);rid=request['request_id']
        if request['agent']=='agent_a' and request['purpose']=='prepare':
            attempts=sum(1 for s in self.seen if s['agent']=='agent_a' and s['purpose']=='prepare')
            if attempts<=self.refuse:raise NativeRefusal('blocked',category='reasoning_extraction')
        if request['purpose']=='prepare':
            return {'request_id':rid,'native_status':'success','has_topic':True,'own_summary':'topic'}
        return {'request_id':rid,'native_status':'success','reply':'fixture','reply_to':request['reply_to'],
                'move':'closure','wants_reply':False,
                'digest_candidate':{'summary':'fixture','shared_message_ids':[rid]}}


class TrialTests(unittest.TestCase):
    def test_config_rejects_runtime_in_protected_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'config.json'
            path.write_text(json.dumps({'runtime':str(root/'persona/runtime'),'content':str(root/'social'),'life_wiki':str(root/'life'),
                'personas':{'agent_a':{'root':str(root/'persona'),'baseline':'base.md'},'agent_b':{'root':str(root/'other'),'baseline':'base.md'}}}))
            with self.assertRaises(BoundaryError):configuration(path)

    def test_cancellation_kills_only_owned_subprocess(self):
        real_popen=subprocess.Popen;children=[]
        def fixture(*args,**kwargs):
            child=real_popen([sys.executable,'-c','import time;time.sleep(30)'],stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
            children.append(child);return child
        with tempfile.TemporaryDirectory() as tmp:
            adapter=CodexAdapter(Path(tmp),cancelled=lambda:True)
            begin=time.monotonic()
            with patch('a2a.codex_adapter.subprocess.Popen',side_effect=fixture):
                with self.assertRaisesRegex(BoundaryError,'stopped by owner'):
                    adapter.call({'agent':'agent_a','purpose':'prepare','request_id':str(uuid.uuid4()),'persona':'test'})
            self.assertLess(time.monotonic()-begin,3)
            self.assertIsNotNone(children[0].poll())
            self.assertNotEqual(children[0].returncode,0)

    def test_owner_stop_rejects_late_chat_and_keeps_partial_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime=root/'runtime';runtime.mkdir()
            data={'runtime':runtime,'content':root/'content'}
            class Adapter:
                def __init__(self,*args,**kw):self.audit=[]
                def call(self,request,timeout):
                    rid=request['request_id']
                    if request['purpose']=='prepare':return {'request_id':rid,'native_status':'success','has_topic':True,'own_summary':'topic'}
                    other=Store(runtime/'state')
                    run=other.db.execute('SELECT id FROM runs').fetchone()[0]
                    Coordinator(other).stop(run);other.close()
                    return {'request_id':rid,'native_status':'success','reply':'late reply','reply_to':None,'move':'closure','wants_reply':False,
                            'digest_candidate':{'summary':'late','shared_message_ids':[rid]}}
            packets={a:{'manifest':[],'content':'persona '+a} for a in ('agent_a','agent_b')}
            captured=[]
            def backup(path,snapshot):captured.append(snapshot);return {'backed_up':True}
            with patch('a2a.trial.preflight',return_value=packets),patch('a2a.trial.save_snapshot',side_effect=backup):
                self.assertEqual(run_trial(data,'topic',adapter_factory=Adapter,sleep=lambda _:None),1)
            self.assertEqual(captured[0]['messages'],[])
            self.assertEqual(captured[0]['stop_reason'],'stopped')

    def test_calibration_has_own_daily_ledger_and_global_lock(self):
        from a2a.trial import status
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data={'runtime':root,'content':root/'content'}
            first=Store(root/'state'); second=Store(root/'state-calibration')
            day='2026-09-07'
            Coordinator(first).start('manual:'+day,day)
            run=Coordinator(second).start('calibration:'+day,day)
            with self.assertRaises(BoundaryError):Coordinator(second).start('calibration:again',day)
            with first.coordinator_lock():self.assertTrue(status(data,True)['worker_running'])
            self.assertEqual(status(data,True)['id'],run)
            self.assertEqual(first.db.execute('SELECT count(*) FROM runs').fetchone()[0],1)
            first.close();second.close()

    def test_related_people_is_bounded_and_does_not_follow_symlinks(self):
        from a2a.material import related_people
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); people=root/'people';people.mkdir()
            for name in 'abcde':(people/(name+'.md')).write_text('fixture')
            (people/'link.md').symlink_to(root/'outside.md');(root/'outside.md').write_text('private')
            result=related_people('[[../outside]] [[link]] [[people/a|A]] [[a]] [[b#part]] [[c]] [[d]]',people)
            self.assertEqual([p.name for p in result],['a.md','b.md','c.md'])

    def _refusal_run(self,tmp,refuse):
        root=Path(tmp);data={'runtime':root,'content':root/'content'}
        packets={a:{'manifest':[],'content':'persona '+a} for a in ('agent_a','agent_b')}
        seen=[];exported=[]
        factory=lambda *a,**k:_RefusingAdapter(*a,refuse=refuse,seen=seen,**k)
        with patch('a2a.trial.preflight',return_value=packets),\
             patch('a2a.trial.save_snapshot',side_effect=lambda p,s:exported.append(s) or {'backed_up':True}):
            code=run_trial(data,'topic',adapter_factory=factory,sleep=lambda _:None)
        report=json.loads((root/'runs'/exported[0]['run_id']/'report.json').read_text())
        return code,seen,exported[0],report

    def test_refused_call_is_resent_and_the_night_still_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            code,seen,snapshot,report=self._refusal_run(tmp,1)
            self.assertEqual(code,0)
            self.assertEqual(snapshot['stop_reason'],'closure')
            # one refused prepare, its resend, agent_b's prepare, then a single closing chat turn
            self.assertEqual(len(seen),4)
            self.assertEqual(snapshot['ledger']['agent_a'],{'prepare':1,'chat':1,'retry':1})
            self.assertEqual(snapshot['ledger']['agent_b']['retry'],0)
            self.assertEqual([r['category'] for r in report['refusals']],['reasoning_extraction'])
            self.assertEqual(report['refusals'][0]['purpose'],'prepare')
            # the resend carries a new attempt ID but the same persona and material
            first,resend=seen[0],seen[1]
            self.assertNotEqual(first['request_id'],resend['request_id'])
            self.assertEqual({k:v for k,v in first.items() if k!='request_id'},
                             {k:v for k,v in resend.items() if k!='request_id'})

    def test_persistent_refusal_stops_after_the_retry_budget_and_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            code,seen,snapshot,report=self._refusal_run(tmp,99)
            self.assertEqual(code,1)
            self.assertEqual(snapshot['stop_reason'],'failed')
            self.assertEqual(len(seen),1+LIMITS['retry'])
            self.assertEqual(snapshot['ledger']['agent_a'],{'prepare':1,'chat':0,'retry':LIMITS['retry']})
            self.assertEqual(len(report['refusals']),1+LIMITS['retry'])
            self.assertIn('retry budget',report['error'])
            self.assertEqual(snapshot['messages'],[])

    def test_long_calibration_stops_at_ten_each_and_retains_prior_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data={'runtime':root,'content':root/'content'}
            packets={a:{'manifest':[],'content':'persona '+a} for a in ('agent_a','agent_b')}
            seen=[]
            class Adapter:
                def __init__(self,*args,**kwargs):self.audit=[]
                def call(self,r,timeout):
                    seen.append(r);rid=r['request_id']
                    if r['purpose']=='prepare':return {'request_id':rid,'native_status':'success','has_topic':True,'own_summary':'topic'}
                    return {'request_id':rid,'native_status':'success','reply':'fixture','reply_to':r['reply_to'],
                            'move':'closure' if r['close'] else 'new_view','wants_reply':not r['close'],
                            'digest_candidate':{'summary':'fixture','shared_message_ids':[rid]}}
            exported=[]
            with patch('a2a.trial.preflight',return_value=packets),patch('a2a.material.collect_material',return_value={}),patch('a2a.trial.save_snapshot',side_effect=lambda p,s:exported.append(s) or {'backed_up':True}):
                for _ in range(2):self.assertEqual(run_trial(data,'topic',calibration=True,adapter_factory=Adapter,sleep=lambda _:None),0)
            self.assertEqual(len(seen),44)
            for s in exported:
                self.assertEqual(len(s['messages']),20)
                for a in ('agent_a','agent_b'):self.assertEqual(s['ledger'][a],{'prepare':1,'chat':10,'retry':0})
            state=Store(root/'state-calibration')
            self.assertEqual(state.db.execute('SELECT count(*) FROM runs').fetchone()[0],2)
            state.close()
