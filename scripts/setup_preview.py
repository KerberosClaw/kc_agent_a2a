#!/usr/bin/env python3
"""Prepare explicit local configuration; never starts services or approves a room implicitly."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from a2a.storage import atomic_write
from discord_party.installation import outside_repository
from discord_party.state import Registry
from discord_party.native import Grant


def prepare(root):
    root=outside_repository(Path(root).absolute())
    if root.exists():raise ValueError('Choose a new installation directory; never overwrite existing state')
    root.mkdir(parents=True,mode=0o700)
    personas={}
    for agent in ('agent_a','agent_b'):
        path=root/'personas'/agent;path.mkdir(parents=True,mode=0o700)
        (path/'patches').mkdir();(path/'journal').mkdir()
        atomic_write(path/'baseline.md',f'# Fictional {agent}\nA curious friend who enjoys small everyday observations. Use your own voice; never claim real shared history.\n')
        atomic_write(path/'persona.json',json.dumps({'baseline':'baseline.md'}))
        personas[agent]={'root':str(path),'baseline':'baseline.md'}
    atomic_write(root/'materials/topic.md','A fictional group is planning a walk on a rainy afternoon.\n')
    config={'runtime':str(root/'runtime'),'content':str(root/'social'),'personas':personas,
            'continuity_root':str(root/'continuity'),'nightly_hours':[3,6],'material_files':[{'id':'example-topic','path':str(root/'materials/topic.md')}]}
    atomic_write(root/'runtime/config.json',json.dumps(config,indent=2)+'\n')
    atomic_write(root/'runtime/state/coordinator.lock', '')
    party=root/'party'
    for name in ('config','state','review','secrets','workers','logs','run','memory','sharing','experiences','backups','evidence'):
        (party/name).mkdir(parents=True,mode=0o700)
    atomic_write(party/'manager.json',json.dumps({'content':config['content'],
          'existing_writer_lock':str(root/'runtime/state/coordinator.lock')},indent=2))
    roots={a:p['root'] for a,p in personas.items()}
    continuation={'runtime':config['runtime'],'party_runtime':str(party),'content':config['content'],
        'continuity_root':str(root/'continuity'),'interactive_roots':roots,'sessions':{},
        'existing_writer_lock':str(root/'runtime/state/coordinator.lock')}
    atomic_write(root/'continuity-config.json',json.dumps(continuation,indent=2)+'\n')
    return {'root':str(root),'config':str(root/'runtime/config.json'),'services_started':False,
            'next':'Review personas/materials; create a PRIVATE encrypted social repo; configure and explicitly approve a room.'}


def room(root,guild,channel,humans,bots):
    root=outside_repository(Path(root).resolve());party=root/'party'
    if not (root/'runtime/config.json').is_file():raise ValueError('Prepare the installation first')
    ids=[guild,channel,*humans,*bots]
    if not all(isinstance(s,str) and s.isdigit() and int(s)>0 for s in ids) or len(set(humans+bots))!=len(humans+bots):
        raise ValueError('Use distinct valid participant IDs')
    if any((party/'config'/(a+'.json')).exists() for a in ('agent_a','agent_b')):
        raise ValueError('Existing room requires the membership migration workflow')
    regs={}
    for agent,bot in zip(('agent_a','agent_b'),bots):
        raw={'guild_id':guild,'channel_id':channel,'human_ids':humans,'bot_owners':dict.fromkeys(bots,humans[0]),'self_id':bot,'protocol':1}
        Registry(**dict(raw,human_ids=tuple(humans)))
        regs[agent]=raw
    review=party/'review'/'initial';review.mkdir(mode=0o700)
    for agent in regs:
        atomic_write(review/(agent+'.md'),f'# Fictional {agent}\nA friendly conversational agent. No real personal history is included.\n')
    atomic_write(review/'room_boundary.md','Only this registered audience may participate. Do not disclose unapproved private sources.\n')
    manifest={'status':'pending','approved_at':None,'approved_by':humans[0],
        'agent_bot_ids':dict(zip(('agent_a','agent_b'),bots)),
        'intended_scope':{'guild_id':guild,'channel_id':channel,'human_ids':humans,'bot_ids':bots},'profile_files':[]}
    atomic_write(review/'manifest.json',json.dumps(manifest,indent=2))
    for agent,raw in regs.items():atomic_write(party/'config'/(agent+'.json'),json.dumps(raw,indent=2))
    return {'review':str(review),'status':'pending','next':'Read/edit both persona cards and the boundary. Approve only if these exact contents and audience may be shared.'}


def approve(root):
    root=outside_repository(Path(root).resolve());party=root/'party';review=party/'review/initial'
    manifest=json.loads((review/'manifest.json').read_text())
    if manifest['status']!='pending' or (party/'review/current').exists():raise ValueError('Already approved; use an explicit revision for later changes')
    entries=[]
    for name in ['agent_a.md','agent_b.md','room_boundary.md']:
        p=review/name
        if p.is_symlink() or not p.is_file() or not p.read_text().strip():raise ValueError('Missing review content')
        entries.append({'name':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        p.chmod(0o600)
    manifest.update(status='approved',approved_at=datetime.now(timezone.utc).isoformat(),profile_files=entries)
    atomic_write(review/'manifest.json',json.dumps(manifest,indent=2))
    for agent in ('agent_a','agent_b'):
        raw=json.loads((party/'config'/(agent+'.json')).read_text());raw['human_ids']=tuple(raw['human_ids'])
        Grant(review,agent,Registry(**raw))
    (party/'review/current').symlink_to(review)
    return {'status':'approved','messages_sent':0,'services_started':False}


def approve_sharing(root):
    from discord_party.continuity import fingerprint
    root=outside_repository(Path(root).resolve());p=root/'party/review/current/manifest.json'
    old=p.read_bytes();manifest=json.loads(old)
    if manifest['status']!='approved':raise ValueError('Approve the room first')
    config_path=root/'continuity-config.json';config=json.loads(config_path.read_text())
    scope={'approved_by':manifest['approved_by'],'human_ids':manifest['intended_scope']['human_ids'],
           'mode':'style_and_nonprivate_material'}
    config['sharing_scope']=scope
    manifest.update(shared_view_policy=fingerprint(scope),revision_kind='style_only',
        supersedes=hashlib.sha256(old).hexdigest(),
        history_since=manifest.get('history_since',manifest['approved_at']),
        approved_at=datetime.now(timezone.utc).isoformat())
    atomic_write(config_path,json.dumps(config,indent=2))
    atomic_write(p,json.dumps(manifest,indent=2))
    return {'sharing':'explicitly approved','next':'Restart only this Party after updating its approved grant; run a continuity tick to curate views.'}


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('prepare');sub.add_parser('approve-room');sub.add_parser('approve-sharing')
    q=sub.add_parser('room');q.add_argument('--guild-id',required=True);q.add_argument('--channel-id',required=True)
    q.add_argument('--human-id',required=True,action='append');q.add_argument('--bot-a-id',required=True);q.add_argument('--bot-b-id',required=True)
    a=p.parse_args()
    if a.command=='prepare':result=prepare(a.root)
    elif a.command=='approve-room':result=approve(a.root)
    elif a.command=='approve-sharing':result=approve_sharing(a.root)
    else:result=room(a.root,a.guild_id,a.channel_id,a.human_id,[a.bot_a_id,a.bot_b_id])
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:print(type(error).__name__+': setup incomplete; review local configuration',file=sys.stderr);sys.exit(1)
