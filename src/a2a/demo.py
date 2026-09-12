"""All three channels in disposable state; no credentials, model, Discord or Git network."""
import json
from pathlib import Path
import tempfile
from .dry_run import walkthrough
from discord_party.state import Registry, State, Event
from discord_party.continuity import Continuity


def run(root):
    first = walkthrough(root/'notes-night')
    registry = Registry('1', '2', ('10',), {'20': '10', '30': '10'}, '20')
    state = State(root/'party', registry, initialize=True)
    continuity = Continuity(root/'continuity')
    try:
        state.synchronized()
        state.ingest(Event('100', '1', '2', '10', 'A fictional conversation about a rainy walk.',
                           '2026-01-01T00:00:00Z', '2026-01-01T00:00:01Z'))
        request = state.begin();assert request
        assert state.finish(request, 'speak', 'A fictional reply.')
        row = state.submit(request);assert row
        state.delivered(request, message_id='101', author_id='20', channel_id='2', nonce=row['nonce'])
        continuity.ingest([{'message_id':'100','channel_id':'2','author_id':'10',
                           'created_at':'2026-01-01T00:00:00Z','content':'A fictional rainy walk.'}])
        job=continuity.reserve('agent_a','demo-grant',force=True)
        assert job
        assert continuity.publish(job, {'overview':'A synthetic conversation.', 'observations':[
            {'kind':'said','text':'Author 10 mentioned a rainy walk.','refs':[str(job['refs'][0])]}]})
        return {'synthetic':True,'network_calls':0,'model_calls':0,'messages_sent':0,
                'note_status':first['note_after'], 'night_messages':len(first['whisper']['messages']),
                'party_quota_remaining':state.snapshot()['remaining'],
                'summary_batches':continuity.db.execute("SELECT count(*) FROM batches WHERE status='ready'").fetchone()[0]}
    finally:state.close();continuity.close()


def main():
    with tempfile.TemporaryDirectory(prefix='agent-a2a-demo-') as temp:
        print(json.dumps(run(Path(temp)),indent=2))


if __name__ == '__main__':main()
