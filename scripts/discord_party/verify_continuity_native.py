#!/usr/bin/env python3
"""Local synthetic native canary: no Discord transport, no canonical writes."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from a2a.storage import atomic_write
from discord_party.continuity import Continuity, encode, fingerprint
from discord_party.continuity_worker import generate
from discord_party.native import Grant, write_private
from discord_party.state import Registry


async def main(root):
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    packet = root / 'grant'; packet.mkdir(mode=0o700)
    profiles = {'agent_a.md': '你是合成測試朋友甲，說話直接，和乙是不同角色。',
                'agent_b.md': '你是合成測試朋友乙，活潑，和甲是不同角色。',
                'room_boundary.md': '只整理提供的合成對話，不可以操作工具或外送。'}
    import hashlib
    manifest = {'status': 'approved', 'approved_at': '2026-09-09T00:00:00Z', 'approved_by': '10',
                'agent_bot_ids': {'agent_a': '20', 'agent_b': '30'},
                'intended_scope': {'guild_id': '1', 'channel_id': '2', 'human_ids': ['10'], 'bot_ids': ['20', '30']},
                'profile_files': [{'name': n, 'sha256': hashlib.sha256(v.encode()).hexdigest()} for n, v in profiles.items()]}
    for name, text in profiles.items(): write_private(packet / name, text)
    write_private(packet / 'manifest.json', encode(manifest))
    state = Continuity(root / 'state')
    texts = [('10', '假如週末大家一起做飯，你們想做什麼？只是想像，還沒約。'),
             ('20', '我負責試吃，乙负责假裝不心疼食材。'),
             ('30', '甲先別偷吃。我也還沒做過這頓飯。'),
             ('10', '補充：不是已經聚餐，也沒有確定日期。這是玩笑話題。')]
    state.ingest([{'channel_id': '2', 'message_id': str(i), 'author_id': author,
                   'created_at': '2026-09-12T00:00:00Z', 'content': text} for i, (author, text) in enumerate(texts, 1)])
    results = {}
    try:
        for agent, bot in [('agent_a', '20'), ('agent_b', '30')]:
            reg = Registry(guild_id='1', channel_id='2', human_ids=('10',), bot_owners={'20': '10', '30': '10'}, self_id=bot)
            grant = Grant(packet, agent, reg)
            job = state.reserve(agent, grant.version, force=True)
            result = await generate(state, job, grant, root / 'workers' / agent)
            document = json.loads(state.db.execute('SELECT body FROM batches WHERE id=?', (job['id'],)).fetchone()[0])
            results[agent] = {'result': result, 'document': document}
            print(encode({'agent': agent, 'published': result['published'], 'audit': result['audit']}), flush=True)
        atomic_write(root / 'results.json', encode(results))
    finally:
        state.close()


if __name__ == '__main__':
    os.umask(0o077)
    asyncio.run(main(Path(sys.argv[1])))
