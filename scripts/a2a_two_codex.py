"""Explicit MBP dry-run: networkless Docker coordinator + two host Codex sessions.

Run from the repository root with PYTHONPATH=src. Uses only synthetic data.
"""
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from a2a.codex_adapter import CodexAdapter
from a2a.storage import atomic_write, encode

repo=Path(__file__).resolve().parents[1]
run_root=repo/'.tmp'/('two-codex-'+uuid.uuid4().hex)
bridge=run_root/'bridge'; results=run_root/'results'
for path in (bridge,bridge/'requests',bridge/'responses',results):
    path.mkdir(parents=True,exist_ok=True);path.chmod(0o777)
adapter=CodexAdapter(run_root/'native-sessions')
name='a2a-real-'+uuid.uuid4().hex[:12]
log=(run_root/'docker.log').open('w')
command=['docker','run','--name',name,'--network','none','--read-only','--user','10001:10001',
         '--cap-drop','ALL','--security-opt','no-new-privileges:true','--pids-limit','64','--memory','256m',
         '--cpus','1','--tmpfs','/tmp:rw,nosuid,nodev,size=128m,mode=1777',
         '--mount',f'type=bind,source={bridge},target=/bridge',
         '--mount',f'type=bind,source={results},target=/results',
         'kc-agent-a2a-dryrun:local','python','-m','a2a.native_run','--bridge','/bridge','--report','/results/report.json']
process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
print(encode({'run_directory':str(run_root),'container':name}),flush=True)
seen=set();errors=[];deadline=time.monotonic()+1200
try:
    while process.poll() is None:
        if time.monotonic()>deadline:raise RuntimeError('overall integration timeout')
        for path in sorted((bridge/'requests').glob('*.json')):
            request_id=str(uuid.UUID(path.stem))
            if request_id in seen:continue
            seen.add(request_id)
            request=json.loads(path.read_text())
            if request.get('request_id') != request_id:raise RuntimeError('bridge request mismatch')
            print(encode({'dispatch':request['agent'],'purpose':request['purpose'],'request_id':request_id}),flush=True)
            try: response={'request_id':request_id,'result':adapter.call(request)}
            except Exception as error:
                errors.append(str(error));response={'request_id':request_id,'error':str(error)}
            dest=bridge/'responses'/path.name
            atomic_write(dest,encode(response));dest.chmod(0o644)
            print(encode({'completed':request['agent'],'success':'error' not in response}),flush=True)
        time.sleep(0.1)
    report_path=results/'report.json'
    report=json.loads(report_path.read_text()) if report_path.exists() else {'passed':False}
    report['native_audit']=adapter.audit
    report['separate_native_sessions']=len(adapter.sessions)==2 and len(set(adapter.sessions.values()))==2
    report['container_exit_code']=process.returncode
    report['bridge_errors']=errors
    report['passed']=report.get('passed',False) and process.returncode==0 and report['separate_native_sessions'] and not errors
    atomic_write(run_root/'combined-report.json',encode(report)+'\n')
    print(encode({'passed':report['passed'],'report':str(run_root/'combined-report.json'),'native_calls':len(adapter.audit)}),flush=True)
finally:
    if process.poll() is None:
        subprocess.run(['docker','stop','--time','1',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        process.wait(timeout=10)
    log.close()
    subprocess.run(['docker','rm',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
raise SystemExit(0 if report['passed'] else 1)
