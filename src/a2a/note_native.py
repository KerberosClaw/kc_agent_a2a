"""Bounded native transcript proof; content is returned only to the note helper, never logged."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .storage import BoundaryError

TAIL_BYTES = 2 * 1024 * 1024


def rows(path, roots):
    path = Path(path)
    if path.is_symlink() or not any(path.resolve().is_relative_to(Path(r).resolve()) for r in roots):
        raise BoundaryError('unapproved native transcript path')
    if not path.exists():
        return []
    with path.open('rb') as f:
        size = path.stat().st_size
        if size > TAIL_BYTES:
            f.seek(size - TAIL_BYTES)
            f.readline()
        data = f.read(TAIL_BYTES)
    result = []
    for line in data.decode('utf-8', errors='replace').splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def channel_text(text):
    match = re.fullmatch(r'<channel\s+[^>]*source="plugin:telegram:telegram"[^>]*>\n?(.*?)\n?</channel>\s*', text, re.S)
    if not match:
        raise BoundaryError('unsupported channel envelope')
    return match.group(1)


def history(path, engine, session, roots):
    turns = {}
    current = None
    for event in rows(path, roots):
        if engine == 'claude':
            if event.get('isSidechain') or event.get('sessionId', session) != session:
                continue
            typ = event.get('type')
            message = event.get('message', {})
            content = message.get('content')
            if typ == 'user' and not event.get('toolUseResult'):
                if isinstance(content, list) and any(b.get('type') == 'tool_result' for b in content if isinstance(b, dict)):
                    continue
                current = event.get('promptId')
                if not current:
                    current = None
                    continue
                origin = event.get('origin', {})
                human = not event.get('isMeta') and (not origin or origin == {'kind': 'human'})
                source = 'interactive'
                if origin == {'kind': 'channel', 'server': 'plugin:telegram:telegram'}:
                    human = True
                    source = 'telegram'
                    try:
                        content = channel_text(content)
                    except (BoundaryError, TypeError):
                        human = False
                if isinstance(content, list):
                    content = '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text')
                turns[current] = {'human': human, 'text': content if isinstance(content, str) else '',
                                  'source': source, 'status': 'active', 'final': ''}
            elif current in turns and typ == 'assistant' and message.get('stop_reason') == 'end_turn':
                turns[current]['final'] = '\n'.join(b.get('text', '') for b in (content or []) if b.get('type') == 'text')
            elif current in turns and typ == 'system' and event.get('subtype') == 'turn_duration':
                # Written after Stop hooks/continuations, unlike an individual Stop callback.
                if turns[current]['final'].strip():
                    turns[current]['status'] = 'success'
        else:
            payload = event.get('payload', {})
            typ = event.get('type')
            sub = payload.get('type')
            if typ == 'event_msg' and sub == 'task_started':
                current = payload.get('turn_id')
                turns[current] = {'human': False, 'text': '', 'source': 'codex', 'status': 'active', 'final': ''}
            elif current in turns and typ == 'response_item' and payload.get('role') == 'user':
                text = '\n'.join(b.get('text', '') for b in payload.get('content', []) if b.get('type') == 'input_text')
                # Only the first root user message; helper output is a tool result, not authorization.
                if not turns[current]['text']:
                    turns[current].update(human=True, text=text)
            elif typ == 'event_msg' and sub in ('task_complete', 'turn_aborted'):
                target = payload.get('turn_id')
                if target in turns:
                    turns[target]['status'] = 'success' if sub == 'task_complete' else 'aborted'
                    turns[target]['final'] = payload.get('last_agent_message') or ''
    return turns, current
