import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock,patch
from a2a.claude_adapter import ClaudeAdapter
from a2a.storage import BoundaryError, NativeRefusal


def _events(session, extra, *, is_error=False, stop_reason='tool_use', structured=None):
    head=[{'type':'system','subtype':'init','session_id':session,'tools':['StructuredOutput'],'mcp_servers':[]}]
    tail=[{'type':'result','subtype':'success','is_error':is_error,'stop_reason':stop_reason,
           'session_id':session,'structured_output':structured}]
    return head+extra+tail


class ClaudeAdapterTests(unittest.TestCase):
    def test_preserves_keychain_user_and_rejects_external_tools(self):
        for tools,allowed in [(['StructuredOutput'],True),(['StructuredOutput','Bash'],False)]:
            with tempfile.TemporaryDirectory() as tmp:
                rid=str(uuid.uuid4());sid=str(uuid.uuid4())
                result={'request_id':rid,'has_topic':True,'own_summary':'fixture'}
                events=[{'type':'system','subtype':'init','session_id':sid,'tools':tools,'mcp_servers':[]},
                        {'type':'result','subtype':'success','is_error':False,'session_id':sid,'structured_output':result}]
                proc=Mock(returncode=0);proc.communicate.return_value=('\n'.join(json.dumps(e) for e in events),'')
                adapter=ClaudeAdapter(Path(tmp));adapter.sessions['agent_a']=sid
                with patch.dict('os.environ',{'HOME':tmp,'USER':'fixture-user','PATH':'/bin','UNRELATED_SECRET':'do-not-forward'},clear=True),patch('a2a.claude_adapter.subprocess.Popen',return_value=proc) as popen:
                    req={'agent':'agent_a','purpose':'prepare','request_id':rid,'persona':'fixture'}
                    if allowed:self.assertEqual(adapter.call(req)['native_status'],'success')
                    else:
                        with self.assertRaisesRegex(BoundaryError,'tool capability'):adapter.call(req)
                    self.assertEqual(popen.call_args.kwargs['env']['USER'],'fixture-user')
                    self.assertNotIn('UNRELATED_SECRET',popen.call_args.kwargs['env'])

    def _run(self,tmp,events,*,returncode=1,session=None):
        rid=str(uuid.uuid4());sid=session or str(uuid.uuid4())
        proc=Mock(returncode=returncode);proc.communicate.return_value=('\n'.join(json.dumps(e) for e in events(sid)),'')
        adapter=ClaudeAdapter(Path(tmp));adapter.sessions['agent_a']=sid
        with patch('a2a.claude_adapter.subprocess.Popen',return_value=proc):
            return adapter,adapter.call({'agent':'agent_a','purpose':'prepare','request_id':rid,'persona':'fixture'})

    def test_server_refusal_is_resendable_and_drops_the_unused_session(self):
        # Shape taken from the 2026-09-16 nightly trace: a reasoning_extraction refusal after
        # the model had already produced thinking, with the rate limiter reporting no rejection.
        refusal=lambda sid:_events(sid,[
            {'type':'rate_limit_event','rate_limit_info':{'status':'allowed_warning','overageStatus':None}},
            {'type':'system','subtype':'model_refusal_no_fallback','api_refusal_category':'reasoning_extraction'},
            {'type':'assistant','message':{'model':'<synthetic>','stop_reason':'refusal','content':[],
                                           'stop_details':{'type':'refusal','category':'reasoning_extraction'}}}],
            is_error=True,stop_reason='refusal')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(NativeRefusal) as caught:self._run(tmp,refusal)
            self.assertEqual(caught.exception.category,'reasoning_extraction')

    def test_refusal_keeps_a_session_that_already_completed_a_turn(self):
        refusal=lambda sid:_events(sid,[{'type':'system','subtype':'model_refusal_no_fallback',
                                         'api_refusal_category':'reasoning_extraction'}],is_error=True,stop_reason='refusal')
        with tempfile.TemporaryDirectory() as tmp:
            sid=str(uuid.uuid4());rid=str(uuid.uuid4())
            proc=Mock(returncode=1);proc.communicate.return_value=('\n'.join(json.dumps(e) for e in refusal(sid)),'')
            adapter=ClaudeAdapter(Path(tmp));adapter.sessions['agent_a']=sid;adapter.completed.add('agent_a')
            with patch('a2a.claude_adapter.subprocess.Popen',return_value=proc):
                with self.assertRaises(NativeRefusal):
                    adapter.call({'agent':'agent_a','purpose':'chat','request_id':rid,'persona':'fixture'})
            self.assertEqual(adapter.sessions['agent_a'],sid)
            adapter.sessions.clear();adapter.completed.clear()
            proc2=Mock(returncode=1);proc2.communicate.return_value=('\n'.join(json.dumps(e) for e in refusal(sid)),'')
            adapter.sessions['agent_a']=sid
            with patch('a2a.claude_adapter.subprocess.Popen',return_value=proc2):
                with self.assertRaises(NativeRefusal):
                    adapter.call({'agent':'agent_a','purpose':'prepare','request_id':str(uuid.uuid4()),'persona':'fixture'})
            self.assertNotIn('agent_a',adapter.sessions)

    def test_rejected_quota_is_reported_without_offering_a_resend(self):
        # Shape taken from the 2026-09-13 nightly trace.
        quota=lambda sid:_events(sid,[{'type':'rate_limit_event','rate_limit_info':{
            'status':'rejected','overageStatus':'rejected','rateLimitType':'seven_day_overage_included'}}],
            is_error=True,stop_reason='stop_sequence')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BoundaryError) as caught:self._run(tmp,quota)
            self.assertNotIsInstance(caught.exception,NativeRefusal)
            self.assertIn('quota',str(caught.exception))

    def test_allowed_call_carrying_a_rejected_overage_flag_still_succeeds(self):
        # Real successful nightly calls report overageStatus 'rejected' while status is 'allowed';
        # reading the overage flag instead of status would fail every healthy run.
        with tempfile.TemporaryDirectory() as tmp:
            rid=str(uuid.uuid4());sid=str(uuid.uuid4())
            payload={'request_id':rid,'has_topic':True,'own_summary':'fixture'}
            events=_events(sid,[{'type':'rate_limit_event','rate_limit_info':{
                'status':'allowed','overageStatus':'rejected','rateLimitType':'five_hour'}}],structured=payload)
            proc=Mock(returncode=0);proc.communicate.return_value=('\n'.join(json.dumps(e) for e in events),'')
            adapter=ClaudeAdapter(Path(tmp));adapter.sessions['agent_a']=sid
            with patch('a2a.claude_adapter.subprocess.Popen',return_value=proc):
                result=adapter.call({'agent':'agent_a','purpose':'prepare','request_id':rid,'persona':'fixture'})
            self.assertEqual(result['native_status'],'success')
            self.assertIn('agent_a',adapter.completed)
