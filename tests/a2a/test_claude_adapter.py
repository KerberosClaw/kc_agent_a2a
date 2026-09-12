import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock,patch
from a2a.claude_adapter import ClaudeAdapter
from a2a.storage import BoundaryError


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
