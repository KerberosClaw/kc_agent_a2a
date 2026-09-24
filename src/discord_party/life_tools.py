"""Per-decision, loopback-only MCP access to an approved Life Wiki projection.

No raw files, filenames, shell, writes, arbitrary URLs or caller-selected audience.
The native worker only has a short-lived capability to this immutable snapshot.
"""
from __future__ import annotations

import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import threading
import time

from .life_context import _contains
from .state import NotReady


SERVER = 'party_life'
TOOL_NAMES = ('search', 'read')
TOOLS = [
    {'name': 'search',
     'description': '查詢 Party 已授權的 Life Wiki：人物現實姓名／別名、基本關係、認識方式、朋友圈、住家生活圈。'
                    '用人名或精簡關鍵詞搜尋；可換詞追查。空 query 可瀏覽 kind 目錄。結果有 id 可用 read 讀取；'
                    '也可查一般生活事件（event）、自己和主人的相處經歷（interaction）；這兩類依事件／記錄日期由新到舊。'
                    '多個關鍵詞必須全部命中；泛問近期相處可用空 query 與 kind=interaction。'
                    'has_more=true 時可用 next_cursor 翻頁，不能把第一頁當作全部。無私人頁面本文。',
     'inputSchema': {'type': 'object', 'properties': {
         'query': {'type': 'string', 'maxLength': 160},
         'kind': {'type': 'string', 'enum': ['all', 'person', 'place', 'event', 'interaction']},
         'cursor': {'type': 'string'},
         'page_size': {'type': 'integer', 'minimum': 1, 'maximum': 10}},
         'required': ['query'], 'additionalProperties': False},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'read',
     'description': '依 search 回傳的 id 讀取完整的已授權短資料；不是私人 wiki 原文。'
                    '可據關聯人名再次 search。not_available 只表示本分享範圍沒有可用資料，不代表私人資料庫沒有。',
     'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string', 'maxLength': 80}},
                     'required': ['id'], 'additionalProperties': False},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
]


class LifeQuery:
    """Frozen data, live authorization, finite calls, explicit pagination."""
    def __init__(self, resolver, validate_grant, *, max_calls=8, timeout=75):
        self.resolver, self.validate_grant = resolver, validate_grant
        self.records = json.loads(json.dumps(resolver.approved_records()))
        self.source_checks = list(getattr(resolver, 'source_checks', []))
        self.used_sources = set()
        self.version = hashlib.sha256(json.dumps(self.records, sort_keys=True).encode()).hexdigest()
        self.by_id = {r['id']: r for r in self.records}
        self.deadline = time.monotonic() + timeout
        self.max_calls, self.calls = max_calls, 0
        self.closed = False
        self.lock = threading.Lock()
        self.cursors = {}
        self.audit = []  # No query text, private content or capability token.

    def _authorize(self):
        if self.closed:
            raise NotReady('Life query closed')
        self.validate_grant()
        self.resolver.validate()
        self.validate_sources()

    def validate_sources(self):
        from .shared_context import validate_sources
        validate_sources(self.source_checks)

    def call(self, name, arguments):
        with self.lock:
            try:
                self._authorize()
            except Exception:
                return {'status': 'unavailable', 'reason': 'authorization_changed'}
            if time.monotonic() >= self.deadline or self.calls >= self.max_calls:
                return {'status': 'limited', 'reason': 'query_budget_reached', 'complete': False}
            self.calls += 1
            try:
                if not isinstance(arguments, dict):
                    raise ValueError
                if name == 'search':
                    result = self.search(**arguments)
                elif name == 'read' and set(arguments) == {'id'}:
                    key = arguments['id']
                    if not isinstance(key, str) or len(key) > 80:
                        raise ValueError
                    record = self.by_id.get(key)
                    if record:
                        self.used_sources.update(record.get('source_refs', []))
                    result = ({'status': 'found', 'record': record} if record else
                              {'status': 'not_available'})
                else:
                    raise ValueError
                self._authorize()
            except (ValueError, TypeError):
                result = {'status': 'invalid_request',
                          'hint': 'search: query string, kind all/person/place/event/interaction, page_size 1..10, '
                                  'cursor from the same query. read: id from search. No other arguments.'}
            except Exception:
                result = {'status': 'unavailable', 'reason': 'authorization_changed'}
            result = dict(result, snapshot_version=self.version)
            self.audit.append({'tool': name if name in TOOL_NAMES else 'unknown', 'status': result['status']})
            return result

    def search(self, query, kind='all', cursor='', page_size=5):
        if (not isinstance(query, str) or len(query) > 160 or kind not in ('all', 'person', 'place', 'event', 'interaction')
                or not isinstance(cursor, str) or type(page_size) is not int or not 1 <= page_size <= 10):
            raise ValueError
        query = query.strip().casefold()
        terms = re.findall(r'[^\s,，、?？]+', query)
        matches = []
        exact = []
        alias_matches = {}
        for record in self.records:
            if kind != 'all' and record['kind'] != kind:
                continue
            aliases = [record['name'], *record.get('aliases', [])]
            alias_hit = any(_contains(query, a.casefold()) for a in aliases)
            # Search only values that the reader may see. Hidden text never
            # influences matches, counts or excerpts.
            text = ' '.join(str(record.get(k, '')) for k in
                            ('name', 'aliases', 'basic_relationship', 'met_via', 'common_circle', 'area',
                             'text', 'event_date', 'recorded_date')).casefold()
            if query and not alias_hit and not all(term in text for term in terms):
                continue
            for alias in ({a.casefold() for a in aliases} if record['kind'] == 'person' else set()):
                if _contains(query, alias):
                    alias_matches.setdefault(alias, set()).add(record['id'])
            is_exact = query in [a.casefold() for a in aliases]
            if is_exact and record['kind'] == 'person':
                exact.append(record['id'])
            matches.append((0 if is_exact else 1 if alias_hit else 2, record))
        matches.sort(key=lambda item: item[1]['id'])
        matches.sort(key=lambda item: item[1].get('event_date') or item[1].get('recorded_date', ''), reverse=True)
        matches.sort(key=lambda item: item[0])
        start = 0
        if cursor:
            saved = self.cursors.get(cursor)
            if saved is None or saved[:3] != (query, kind, page_size):
                raise ValueError
            start = saved[3]
        page = [r for _, r in matches[start:start + page_size]]
        for record in page:
            self.used_sources.update(record.get('source_refs', []))
        more = start + len(page) < len(matches)
        next_cursor = ''
        if more:
            next_cursor = secrets.token_urlsafe(18)
            self.cursors[next_cursor] = (query, kind, page_size, start + len(page))
        ambiguous = len(exact) > 1 or any(len(ids) > 1 for ids in alias_matches.values())
        return {'status': 'ambiguous' if ambiguous else 'found' if matches else 'not_available',
                'items': [{'id': r['id'], 'kind': r['kind'], 'name': r['name'],
                           'summary': r.get('basic_relationship') or r.get('area') or r.get('text') or r['name'],
                           **({'event_date': r['event_date'], 'owner': r['owner']} if 'event_date' in r else {})} for r in page],
                'total': len(matches), 'has_more': more, 'next_cursor': next_cursor,
                **({'hint': '所有關鍵詞需同時命中。可縮短 query，或用空 query 瀏覽同一 kind，再用 next_cursor 翻頁；一次零命中不能證明沒有相關資料。'} if not matches else {})}

    def close(self):
        with self.lock:
            self.closed = True


class LifeToolServer:
    """Small MCP Streamable HTTP JSON-response subset for two read-only tools."""
    def __init__(self, query):
        self.query = query
        self.token = secrets.token_urlsafe(32)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, code, body=None):
                data = json.dumps(body, ensure_ascii=False).encode() if body is not None else b''
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _allowed(self):
                return (self.path == '/mcp' and self.headers.get('Host') == owner.authority
                        and not self.headers.get('Origin')
                        and hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + owner.token))

            def do_GET(self):
                self._reply(405 if self._allowed() else 403)

            def do_DELETE(self):
                self._reply(405 if self._allowed() else 403)

            def do_POST(self):
                if not self._allowed():
                    self._reply(403); return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 8192:
                        raise ValueError
                    obj = json.loads(self.rfile.read(size))
                    if not isinstance(obj, dict) or obj.get('jsonrpc') != '2.0':
                        raise ValueError
                except (ValueError, UnicodeError):
                    self._reply(400); return
                if 'id' not in obj:
                    self._reply(202); return
                method, params = obj.get('method'), obj.get('params', {})
                if not isinstance(params, dict):
                    self._reply(400); return
                if method == 'initialize':
                    requested = params.get('protocolVersion')
                    version = requested if requested in ('2025-03-26', '2025-06-18', '2025-11-25') else '2025-11-25'
                    result = {'protocolVersion': version, 'capabilities': {'tools': {}},
                              'serverInfo': {'name': SERVER, 'version': '1.0'}}
                elif method == 'ping':
                    result = {}
                elif method == 'tools/list':
                    result = {'tools': TOOLS}
                elif method == 'tools/call':
                    value = owner.query.call(params.get('name'), params.get('arguments', {}))
                    result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}],
                              'isError': value['status'] in ('invalid_request', 'unavailable', 'limited')}
                else:
                    self._reply(200, {'jsonrpc': '2.0', 'id': obj['id'],
                                      'error': {'code': -32601, 'message': 'Method not found'}}); return
                self._reply(200, {'jsonrpc': '2.0', 'id': obj['id'], 'result': result})

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.authority = '127.0.0.1:' + str(self.httpd.server_port)
        self.url = 'http://' + self.authority + '/mcp'
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={'poll_interval': 0.05}, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.query.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=1)
