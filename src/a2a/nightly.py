"""One local nightly window, explicit enable/disable, no model polling or automatic retry."""
from __future__ import annotations

import hashlib
import json
import plistlib
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .storage import BoundaryError,Store,atomic_write,encode
from .trial import run_trial,status
from .material import collect_material
from .claude_adapter import MixedAdapter


def control(data):
    p=data['runtime']/'nightly-control.json'
    return json.loads(p.read_text()) if p.exists() else {'enabled':False}


def quiet_hours(home, config=None):
    config = config or {}
    start,end = config.get('nightly_hours', [3,6])
    if type(start)!=int or type(end)!=int or not 0<=start<24 or not 0<=end<24 or start==end:
        raise BoundaryError('invalid effective quiet hours')
    return start,end


def eligible(now,hours):
    a,b=hours;h=now.hour
    return a<=h<b if a<b else h>=a or h<b


def fingerprints(material):
    return {a:hashlib.sha256(encode({k:v for k,v in m.items() if k not in ('generated_at','relationship_digests')}).encode()).hexdigest() for a,m in material.items()}


def tick(data,now=None):
    now=now or datetime.now(ZoneInfo('Asia/Taipei'))
    c=control(data)
    if not c.get('enabled'):return {'status':'disabled'}
    hours=quiet_hours(Path.home(),data)
    if not eligible(now,hours):return {'status':'outside_window'}
    if status(data).get('worker_running'):return {'status':'running'}
    day=now.date().isoformat()
    window_day=now.date()-timedelta(days=1) if hours[0]>hours[1] and now.hour<hours[1] else now.date()
    window='nightly:'+window_day.isoformat()
    store=Store(data['runtime']/'state')
    try:
        if store.db.execute('SELECT 1 FROM runs WHERE day=? OR window=?',(day,window)).fetchone():return {'status':'window_used'}
        previous={r['agent']:r['value'] for r in store.db.execute('SELECT * FROM cursors')}
    finally:store.close()
    material=collect_material(data,now);fp=fingerprints(material)
    if previous==fp:
        s=Store(data['runtime']/'state')
        try:
            from .coordinator import Coordinator
            with s.coordinator_lock():
                coord=Coordinator(s);run=coord.start(window,day);coord.finish(run,'skipped_no_material')
        finally:s.close()
        return {'status':'skipped_no_material'}
    cancelled=lambda:datetime.now(ZoneInfo('Asia/Taipei')).date()!=now.date() or not control(data).get('enabled') or not eligible(datetime.now(ZoneInfo('Asia/Taipei')),quiet_hours(Path.home(),data))
    code=run_trial(data,'私人悄悄話',adapter_factory=MixedAdapter,nightly=True,external_cancelled=cancelled,material_override=material,candidate_cursors=fp,window_id=window)
    latest=status(data,False);report=latest.get('report',{})
    result={'status':report.get('status','failed'),'run_id':latest.get('id'),'backed_up':report.get('backup',{}).get('backed_up',False)}
    atomic_write(data['runtime']/'nightly-last.json',encode(result))
    if code or not result['backed_up']:raise BoundaryError('nightly failed or backup incomplete; see local report')
    return result
