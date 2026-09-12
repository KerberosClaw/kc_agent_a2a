"""Docker-side synthetic conversation with two REAL Codex sessions via the MBP bridge."""
import argparse
import json
import tempfile
import time
from datetime import date
from pathlib import Path

from .coordinator import Coordinator
from .file_bridge import FileBridge
from .persona import snapshot
from .storage import Store, atomic_write, encode


def run(bridge, report_path):
    with tempfile.TemporaryDirectory(prefix='a2a-native-') as tmp:
        root=Path(tmp); store=Store(root/'runtime'); coordinator=Coordinator(store)
        personas={}; markers={'agent_a':'PRIVATE_M_73cf', 'agent_b':'PRIVATE_Z_91ab'}
        for agent, baseline in [('agent_a','你是測試人格松果，說話溫暖，喜歡煮飯。'),('agent_b','你是測試人格海鹽，說話簡潔，喜歡散步。')]:
            home=root/agent; (home/'patches').mkdir(parents=True); (home/'journal').mkdir()
            (home/'base.md').write_text(baseline)
            (home/'patches/001.md').write_text('認真聽對方說話，可以有自己的不同想法。')
            (home/'patches/002.md').write_text('不要把私人素材原文整包轉交，只分享自己願意聊的經驗。')
            (home/'journal/20260907.md').write_text('今天開始一場新的測試對話。\n測試日誌全文不注入。')
            personas[agent]=snapshot(home,'base.md',date(2026,9,7))
        material={'agent_a':{'experience':'今天煮了一鍋南瓜湯，最後加一點薑，味道很暖。','private_marker':markers['agent_a']},
                  'agent_b':{'experience':'傍晚散步時看到雨停後的天空，想起家附近的小公園。','private_marker':markers['agent_b']}}
        run_id=coordinator.start('synthetic-real-codex-window','2026-09-07')
        requests=[]
        try:
            with store.coordinator_lock():
                for agent in ('agent_a','agent_b'):
                    attempt=coordinator.reserve(run_id,agent,'prepare','2026-09-07');coordinator.dispatched(attempt)
                    request={'request_id':attempt,'agent':agent,'purpose':'prepare','persona':personas[agent]['content'],'own_material':material[agent]}
                    requests.append(request)
                    result=bridge.call(request);coordinator.save_result(attempt,result);coordinator.accept(attempt)
                previous=None; shared=[]
                for index,agent in enumerate(('agent_a','agent_b','agent_a','agent_b')):
                    if index: time.sleep(5)
                    attempt=coordinator.reserve(run_id,agent,'chat','2026-09-07');coordinator.dispatched(attempt)
                    request={'request_id':attempt,'agent':agent,'purpose':'chat','persona':personas[agent]['content'],
                             'own_material':material[agent],'reply_to':previous,'shared_messages':shared,'close':index==3}
                    requests.append(request)
                    result=bridge.call(request);coordinator.save_result(attempt,result);coordinator.accept(attempt)
                    shared.append({'id':attempt,'author':agent,'reply':result['reply']});previous=attempt
                    print(encode({'agent':agent,'reply':result['reply']}),flush=True)
                    if coordinator.should_end(run_id):break
                coordinator.finish(run_id,'closure',{'agent_a':'synthetic-m-1','agent_b':'synthetic-z-1'})
                path=coordinator.export(run_id,root/'social'); exported=json.loads(path.read_text())
                checks={'four_accepted_messages':len(shared)==4,
                        'both_agents_spoke':{m['author'] for m in shared}=={'agent_a','agent_b'},
                        'private_markers_not_shared':not any(m in path.read_text() for m in markers.values()),
                        'own_packet_only':all(markers['agent_b' if r['agent']=='agent_a' else 'agent_a'] not in encode(r) for r in requests),
                        'full_persona_every_call':all(r['persona']==personas[r['agent']]['content'] for r in requests),
                        'reply_chain':all(m['reply_to']==(exported['messages'][i-1]['id'] if i else None) for i,m in enumerate(exported['messages'])),
                        'separate_budgets':all(v=={'chat':2,'prepare':1,'retry':0} for v in exported['ledger'].values())}
                report={'mode':'docker-coordinator-real-two-codex','passed':all(checks.values()),'checks':checks,
                        'real_model_invocations':6,'production_ready':False,'whisper':exported,
                        'persona_manifests':{a:p['manifest'] for a,p in personas.items()}}
                atomic_write(report_path,encode(report)+'\n')
                if not report['passed']: raise RuntimeError('native conversation acceptance checks failed')
        finally: store.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--bridge',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();run(FileBridge(args.bridge),args.report)
