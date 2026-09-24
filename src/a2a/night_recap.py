"""Deterministic common read surface over two authored nightly digests."""
from __future__ import annotations

import hashlib

from .storage import BoundaryError, encode


AUTHORS = ('agent_a', 'agent_b')
LABELS = {'agent_a': 'Agent A', 'agent_b': 'Agent B'}


def build(snapshot):
    """Build one recap without rewriting either author's original digest."""
    if snapshot.get('mode') != 'nightly' or snapshot.get('stop_reason') != 'closure':
        raise BoundaryError('common recap requires a completed nightly run')
    run_id = snapshot.get('run_id')
    if not isinstance(run_id, str) or not run_id:
        raise BoundaryError('common recap requires a run id')
    messages = snapshot.get('messages')
    if not isinstance(messages, list):
        raise BoundaryError('common recap requires messages')
    allowed = {m.get('id') for m in messages if isinstance(m, dict)}
    digests = snapshot.get('digests')
    if not isinstance(digests, dict):
        raise BoundaryError('common recap requires authored digests')
    perspectives = []
    for author in AUTHORS:
        item = digests.get(author)
        if item is None:
            continue
        if (not isinstance(item, dict) or item.get('author') != author
                or not isinstance(item.get('summary'), str)
                or not item['summary'].strip() or len(item['summary']) > 400):
            raise BoundaryError('invalid authored digest')
        refs = item.get('shared_message_ids')
        if not isinstance(refs, list) or any(ref not in allowed for ref in refs):
            raise BoundaryError('invalid common recap provenance')
        perspectives.append({'author': author, 'summary': item['summary'].strip(),
                             'shared_message_ids': refs})
    if not perspectives:
        raise BoundaryError('common recap has no authored digest')
    summary = '\n'.join(f"{LABELS[p['author']]}的記錄：{p['summary']}" for p in perspectives)
    source = {'run_id': run_id, 'night_label': snapshot.get('night_label'),
              'started_at': snapshot.get('started_at'), 'ended_at': snapshot.get('ended_at'),
              'stop_reason': snapshot.get('stop_reason'), 'perspectives': perspectives}
    return {'schema_version': 1, **source, 'summary': summary,
            'source_fingerprint': hashlib.sha256(encode(source).encode()).hexdigest()}


def validate(recap, snapshot):
    expected = build(snapshot)
    if recap != expected:
        raise BoundaryError('common recap does not match its immutable source')
    return recap
