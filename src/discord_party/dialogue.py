"""Bound nightly recollection to approved, dated evidence and the current human turn."""
from __future__ import annotations

from datetime import datetime, time, timedelta
import re
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo('Asia/Taipei')
CONTRIBUTIONS = ('answer', 'new', 'correction', 'none')


def _at(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(TAIPEI) if result.utcoffset() is not None else None
    except (ValueError, AttributeError, TypeError):
        return None


def _request(text, reference):
    # Only a human question about the agents' night conversation selects evidence.
    if re.search(r'今晚|明晚|明天|打算|準備|計[畫劃]', text):
        return None
    if not re.search(r'聊[了過的些點\s]*(啥|什麼|什么)|聊了|聊過|說了什麼|內容|回顧|摘要|記得', text):
        return None
    if not ('夜聊' in text or (re.search(r'昨晚|昨夜|昨天晚上|前晚|前天晚上|凌晨|今早|今天早上', text)
                             and re.search(r'聊.{0,4}(啥|什麼|什么|了)|聊了|聊過|說了什麼', text))):
        return None
    if reference is None:
        return {'status': 'unavailable', 'reason': 'unknown_reference_time'}
    day = reference.date()
    explicit = re.search(r'(?:(\d{4})[-/年])?(\d{1,2})[-/月](\d{1,2})日?', text)
    if explicit:
        try:
            day = day.replace(year=int(explicit[1] or day.year), month=int(explicit[2]), day=int(explicit[3]))
        except ValueError:
            return {'status': 'unavailable', 'reason': 'invalid_date'}
        dawn = bool(re.search(r'凌晨|早上|清晨', text))
    elif re.search(r'前晚|前天晚上', text):
        day -= timedelta(days=2)
        dawn = False
    elif re.search(r'昨晚|昨夜|昨天晚上|昨天.*夜聊', text):
        day -= timedelta(days=1)
        dawn = False
    elif re.search(r'今天凌晨|今早|今天早上|凌晨', text):
        dawn = True
    else:
        return {'selection': 'latest_completed', 'timezone': 'Asia/Taipei'}
    start = datetime.combine(day, time(0 if dawn else 18), TAIPEI)
    end = datetime.combine(day if dawn else day + timedelta(days=1), time(12), TAIPEI)
    return {'selection': 'time_window', 'timezone': 'Asia/Taipei',
            'start': start.isoformat(), 'end': end.isoformat()}


def _select(materials, request, reference):
    if reference is None or request.get('status') == 'unavailable':
        return []
    def eligible(source):
        at = _at(source.get('occurred_at'))
        if source.get('origin') not in ('night', 'night_recap') or at is None or at > reference:
            return False
        if request['selection'] == 'time_window':
            return _at(request['start']) <= at < _at(request['end'])
        return True
    candidates = [m for m in materials if m.get('sources') and all(eligible(s) for s in m['sources'])]
    if request['selection'] == 'latest_completed' and candidates:
        newest = max(_at(s['occurred_at']) for m in candidates for s in m['sources'])
        candidates = [m for m in candidates if all(_at(s['occurred_at']) == newest for s in m['sources'])]
    return candidates


def prepare_chat(payload):
    """Pure projection; never read private sources or alter stored room history."""
    result = dict(payload)
    messages = payload['messages']
    humans = [i for i, row in enumerate(messages) if row.get('role') == 'human']
    if not humans:
        return result
    index = humans[-1]
    human = messages[index]
    reference = _at(human.get('created_at'))
    turn = messages[index:]
    result['human_turn'] = {'message_id': human['message_id'],
                            'created_at': reference.isoformat() if reference else None,
                            'timezone': 'Asia/Taipei',
                            'own_reply_count': sum(m.get('role') == 'self' for m in turn),
                            'messages': turn}
    request = _request(human.get('content', ''), reference)
    if request is not None:
        materials = _select(payload['shared_materials'], request, reference)
        result['night_recall'] = dict(request, status='available' if materials else 'unavailable')
        result['shared_materials'] = materials
        # Party snippets/digests are not evidence about a separate night session.
        result['prior_room_excerpts'] = []
        result['prior_room_experiences'] = []
        result['approved_life_context'] = []
        result['messages'] = turn
    elif (re.search(r'(?:我們家|我家|家裡|住家).{0,6}(?:附近|周邊)', human.get('content', ''))
          and re.search(r'找|推薦|上網|查', human.get('content', ''))):
        # A self-contained local search is a fresh task. Keep old chat in the
        # ledger, but do not feed old bot recommendations back as its answer.
        result['prior_room_excerpts'] = []
        result['prior_room_experiences'] = []
        result['messages'] = turn
    return result


def settle_contribution(result, payload):
    """Silence empty handoffs and a second rendition of one's own recap answer."""
    if result['contribution'] == 'none':
        return ('close' if result['action'] == 'close' and not result['content'] else 'pass'), ''
    if (payload.get('night_recall') and result['contribution'] == 'answer'
            and payload['human_turn']['own_reply_count']):
        return 'pass', ''
    return result['action'], result['content']
