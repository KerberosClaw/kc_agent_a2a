"""Manual native trial: separate runtime and content, read-only persona sources, no schedules."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .codex_adapter import CodexAdapter
from .coordinator import Coordinator, LIMITS
from .notes import AGENTS
from .persona import snapshot
from .social import save_snapshot, verify_repository
from .storage import BoundaryError, NativeRefusal, Store, atomic_write, encode

RETRY_BACKOFF_SECONDS=30

CALIBRATION_TOPIC='你們可以從各自拿到的生活與語料記錄挑想聊的事，聊聊主人，互相接話；不用刻意完成議程。'

DEFAULT_TOPIC='如果能有一個完全不用趕進度的晚上，你會想怎麼過？也聽聽對方的想法。'


def configuration(path):
    data=json.loads(path.read_text())
    for name in ('runtime','content'):
        data[name]=Path(data[name]).resolve()
    if set(data['personas'])!=AGENTS:raise BoundaryError('exactly two persona sources required')
    for values in data['personas'].values():values['root']=Path(values['root']).resolve()
    protected=[v['root'] for v in data['personas'].values()]
    if data.get('life_wiki'):
        data['life_wiki']=Path(data['life_wiki']).resolve();protected.append(data['life_wiki'])
    for item in data.get('material_files', []):
        protected.append(Path(item['path']).resolve())
    if any(data[k].is_relative_to(p) or p.is_relative_to(data[k]) for k in ('runtime','content') for p in protected):
        raise BoundaryError('runtime/content overlap with protected sources')
    if data['runtime']==data['content'] or data['runtime'].is_relative_to(data['content']) or data['content'].is_relative_to(data['runtime']):
        raise BoundaryError('runtime and content must be separate')
    data['runtime'].mkdir(parents=True,exist_ok=True,mode=0o700);data['runtime'].chmod(0o700)
    return data


def preflight(data):
    if sys.platform!='darwin' or os.environ.get('A2A_GUARDED')!='1':
        raise BoundaryError('use the installed guarded agent-a2a launcher on macOS')
    verify_repository(data['content'])
    now=datetime.now(ZoneInfo('Asia/Taipei'))
    packets={a:snapshot(v['root'],v['baseline'],now.date()) for a,v in data['personas'].items()}
    # Do not log contents. Snapshot loading is a read-only operation.
    return packets


def stop_requested(store,run):
    row=store.db.execute('SELECT stop FROM runs WHERE id=?',(run,)).fetchone()
    return bool(row and row[0])


def run_trial(data,topic, *, adapter_factory=CodexAdapter, sleep=time.sleep, calibration=False, nightly=False, external_cancelled=None, material_override=None, candidate_cursors=None, window_id=None):
    if not topic.strip() or len(topic)>2000:raise BoundaryError('topic must contain 1..2000 characters')
    packets=preflight(data)
    store=Store(data['runtime']/('state-calibration' if calibration else 'state'));coordinator=Coordinator(store)
    global_store=Store(data['runtime']/'state')
    started=datetime.now(ZoneInfo('Asia/Taipei'))
    day=started.date().isoformat()
    try:
        with global_store.coordinator_lock():
            run=coordinator.start(window_id or ('calibration:'+str(uuid.uuid4()) if calibration else ('nightly:' if nightly else 'manual:')+day),day,calibration=calibration)
            run_root=data['runtime']/'runs'/run;run_root.mkdir(parents=True,mode=0o700)
            atomic_write(data['runtime']/'latest.json',encode({'run_id':run,'day':day,'pid':os.getpid(),'calibration':calibration}))
            atomic_write(run_root/'persona-manifests.json',encode({a:p['manifest'] for a,p in packets.items()}))
            deadline=time.monotonic()+3600
            cancelled=lambda:stop_requested(store,run) or time.monotonic()>=deadline or bool(external_cancelled and external_cancelled())
            adapter=adapter_factory(run_root/'native',synthetic=False,cancelled=cancelled,authorized_material=calibration or nightly)
            reason='limit';error=None;attempt=None
            shared=[];prepared={};refusals=[]

            def dispatch(agent,kind,build):
                """One native call. A server-side refusal is the only failure worth resending, so the
                identical request is sent again under a fresh attempt ID until the retry budget ends."""
                nonlocal attempt
                attempt=coordinator.reserve(run,agent,kind,day);resent=0
                while True:
                    coordinator.dispatched(attempt)
                    try:return adapter.call(build(attempt),timeout=180)
                    except NativeRefusal as refused:
                        coordinator.fail(attempt,confirmed=True)
                        refusals.append({'agent':agent,'purpose':kind,'request_id':attempt,
                                         'category':refused.category,'resend':resent})
                        if cancelled():raise BoundaryError('owner stop or run deadline')
                        try:attempt=coordinator.reserve(run,agent,'retry',day,attempt)
                        except BoundaryError as spent:
                            raise BoundaryError('refused '+str(resent+1)+' time(s); retry budget of '
                                                +str(LIMITS['retry'])+' is spent: '+str(refused)) from spent
                        resent+=1
                        for _ in range(RETRY_BACKOFF_SECONDS*2):  # short steps stay interruptible
                            if cancelled():break
                            sleep(0.5)
            try:
                materials={}
                if calibration or nightly:
                    from .material import collect_material
                    materials=material_override if material_override is not None else collect_material(data)
                    from .digests import recent
                    relationships=recent(data['content'])
                    for m in materials.values():m['relationship_digests']=relationships
                    atomic_write(run_root/'material-packets.json',encode(materials))
                for agent in ('agent_a','agent_b'):
                    if cancelled():raise BoundaryError('owner stop or run deadline')
                    result=dispatch(agent,'prepare',lambda rid,agent=agent:{
                        'request_id':rid,'agent':agent,'purpose':'prepare','persona':packets[agent]['content'],
                        'own_material':materials.get(agent,{'topic':topic,'instruction':'只整理你對指定話題的想法；不取私聊，不轉述主人的私人資料。'})})
                    if not coordinator.save_result(attempt,result):raise BoundaryError('stopped before acceptance')
                    coordinator.accept(attempt);prepared[agent]=result['has_topic'];attempt=None
                opener='agent_a' if prepared['agent_a'] else 'agent_b'
                other='agent_b' if opener=='agent_a' else 'agent_a'
                order=(opener,other)*10 if any(prepared.values()) else ()
                if not order:reason='skipped_no_topic'
                for index,agent in enumerate(order):
                    if index:
                        for _ in range(10):
                            if cancelled():break
                            sleep(0.5)
                    if cancelled():raise BoundaryError('owner stop or run deadline')
                    result=dispatch(agent,'chat',lambda rid,agent=agent,index=index:{
                        'request_id':rid,'agent':agent,'purpose':'chat','persona':packets[agent]['content'],
                        'own_material':materials.get(agent,{'topic':topic}),'shared_messages':shared,
                        'reply_to':shared[-1]['id'] if shared else None,'close':index==19,
                        'remaining_chat_slots':10-index//2,'chat_limit_per_agent':10})
                    if not coordinator.save_result(attempt,result):raise BoundaryError('stopped before acceptance')
                    coordinator.accept(attempt)
                    shared.append({'id':attempt,'author':agent,'reply':result['reply']});attempt=None
                    if coordinator.should_end(run):reason='closure';break
            except Exception as exc:
                reason='stopped' if stop_requested(store,run) else 'failed'
                error=type(exc).__name__+': '+str(exc)
                if attempt:
                    row=store.db.execute('SELECT status FROM attempts WHERE id=?',(attempt,)).fetchone()
                    if row and row[0] in ('reserved','running'):coordinator.fail(attempt,confirmed=False)
            coordinator.finish(run,reason,candidate_cursors=candidate_cursors,metadata={'started_at':started.isoformat(),'ended_at':datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),
                'mode':'nightly' if nightly else ('calibration' if calibration else 'manual'),
                'night_label':(started.date()-timedelta(days=1)).isoformat() if nightly and started.hour<12 else None})
            path=coordinator.export(run,run_root/'terminal')
            final=json.loads(path.read_text())
            report={'run_id':run,'status':reason,'error':error,'messages':len(shared),
                    'calibration':calibration,'display_policy':'explicit_calibration_only',
                    'ledger':final['ledger'],'native_audit':adapter.audit,'persona_sources_read_only':True,
                    'life_wiki_writes':False,'automatic_schedule':nightly,'refusals':refusals}
            try:report['backup']=save_snapshot(data['content'],final)
            except Exception as exc:report['backup']={'backed_up':False,'error':type(exc).__name__+': '+str(exc)}
            atomic_write(run_root/'report.json',encode(report)+'\n')
            print(encode({'run_id':run,'status':reason,'messages':len(shared),'report':str(run_root/'report.json'),
                          'backed_up':report['backup'].get('backed_up',False)}))
            return 0 if reason in ('closure','limit','skipped_no_topic') else 1
    finally:
        store.close();global_store.close()


def status(data, calibration=None):
    if calibration is None:
        latest=data['runtime']/'latest.json'
        calibration=json.loads(latest.read_text()).get('calibration',False) if latest.exists() else False
    store=Store(data['runtime']/('state-calibration' if calibration else 'state'))
    try:
        row=store.db.execute('SELECT * FROM runs ORDER BY rowid DESC LIMIT 1').fetchone()
        if not row:return {'status':'idle','automatic_schedule':False}
        info=dict(row)
        info['calls']=[dict(r) for r in store.db.execute('SELECT agent,kind,count(*) AS count FROM attempts WHERE run=? GROUP BY agent,kind',(row['id'],))]
        report=data['runtime']/'runs'/row['id']/'report.json'
        if report.exists():info['report']=json.loads(report.read_text())
        try:
            guard=Store(data['runtime']/'state')
            try:
                with guard.coordinator_lock():info['worker_running']=False
            finally:guard.close()
        except BoundaryError:info['worker_running']=True
        return info
    finally:store.close()


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('preflight','status','stop','tick','enable','disable'):sub.add_parser(name)
    for name in ('start','run'):
        p=sub.add_parser(name);p.add_argument('--topic',default=DEFAULT_TOPIC);p.add_argument('--calibrate',action='store_true')
    args=parser.parse_args();data=configuration(args.config)
    if args.command=='preflight':
        packets=preflight(data)
        print(encode({'ready':True,'guarded':True,'automatic_schedule':False,
                      'personas':{a:{'files':len(p['manifest']),'characters':len(p['content'])} for a,p in packets.items()}}))
    elif args.command=='tick':
        from .nightly import tick
        print(encode(tick(data)))
    elif args.command in ('enable','disable'):
        atomic_write(data['runtime']/'nightly-control.json',encode({'enabled':args.command=='enable','display_policy':'owner_reads_during_calibration_then_surprise'}))
        print(encode({'enabled':args.command=='enable'}))
    elif args.command=='status':
        from .nightly import control
        info=status(data);info['nightly_control']=control(data);print(encode(info))
    elif args.command=='stop':
        stopped=[]
        for name in ('state','state-calibration'):
            store=Store(data['runtime']/name)
            try:
                rows=store.db.execute("SELECT id FROM runs WHERE status='active'").fetchall()
                for row in rows:Coordinator(store).stop(row['id']);stopped.append(row['id'])
            finally:store.close()
        print(encode({'stop_requested':stopped,'claude_tmux_touched':False}))
    elif args.command=='start':
        preflight(data)
        current=status(data, args.calibrate)
        if current.get('worker_running'):raise BoundaryError('a trial is already running')
        day=datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
        if not args.calibrate and current.get('day')==day:raise BoundaryError('today already has a trial; no automatic repeat')
        log=(data['runtime']/'worker.log').open('a')
        process=subprocess.Popen([sys.executable,'-m','a2a.trial','--config',str(args.config),'run','--topic',args.topic,*(['--calibrate'] if args.calibrate else [])],
                                 stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        log.close()
        for _ in range(50):
            latest=data['runtime']/'latest.json'
            if latest.exists() and json.loads(latest.read_text()).get('pid')==process.pid:break
            if process.poll() is not None:raise BoundaryError('trial startup failed; inspect worker.log')
            time.sleep(0.1)
        else:raise BoundaryError('trial startup not yet confirmed; inspect status before retrying')
        print(encode({'started_pid':process.pid,'automatic_schedule':False}))
    else:raise SystemExit(run_trial(data,CALIBRATION_TOPIC if args.calibrate else args.topic,calibration=args.calibrate))


if __name__=='__main__':
    try:main()
    except BoundaryError as exc:print(encode({'error':str(exc)}),file=sys.stderr);raise SystemExit(1)
