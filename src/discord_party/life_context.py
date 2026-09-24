"""Audience-bound, on-demand Life Wiki context for Discord Party.

The room model never receives a wiki path or page body.  This trusted host-side
resolver reads only person frontmatter, applies a deterministic deny policy and
returns short cards.  A private policy file is the owner's approval record.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .state import NotReady


BLOCKED = re.compile(
    r'性|情慾|情色|色情|親密|調情|曖昧|約|發生過|炮友|砲友|互摸|摸過|尻|口交|吹屌|裸|'
    r'開放關係|dom|sub|主人|狗勾|按摩|'
    r'健康|病史|用藥|就醫|診斷|地址|門牌|座標|即時位置|私訊|對話原文|'
    r'密碼|憑證|公司機密|工作機密|薪資|職稱|任職|公司'
)
DEEP_CUES = re.compile(r'詳細|展開|多說|多一點|更多|參考|來源|連結|查一下|查查|上網|搜尋|補.{0,3}脈絡|分析')
INFO_CUES = re.compile(r'推薦|哪裡|哪個|多少|價格|怎麼|如何|為什麼|是什麼|有什麼|附近|餐廳|資料|[?？]')
HOME_CUES = re.compile(r'(家裡|我們家|住家).{0,5}(附近|周邊)|(附近|周邊).{0,8}(吃|餐廳|店|推薦)')
SAFE_RELATION = re.compile(
    r'朋友|熟人|舊識|同學|同事|飯友|飯咖|車友|鄰居|伴侶|男友|女友|丈夫|妻子|夫妻|'
    r'partner|(?:^|\s)b(?:\s|$)|同行者|同溫層|點頭之交|不熟|恩人|推薦人|委託人', re.I
)


def response_mode(context):
    """Choose the owner-approved output budget from the latest human turn."""
    text = ''
    for row in reversed(context):
        if row.get('role') == 'human':
            text = row.get('content', '')
            break
    if DEEP_CUES.search(text):
        return 'deep'
    if INFO_CUES.search(text):
        return 'informational'
    return 'casual'


def response_limit(mode):
    return {'casual': 80, 'informational': 200, 'deep': 500}[mode]


def _private_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise NotReady('Life context policy must be a private regular file')
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _frontmatter(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 262144:
        raise NotReady('Invalid Life Wiki person page')
    raw = path.read_bytes()
    text = raw.decode('utf-8')
    if not text.startswith('---\n') or '\n---\n' not in text[4:]:
        return {}, hashlib.sha256(raw).hexdigest()
    block = text[4:text.index('\n---\n', 4)]
    values = {}
    for line in block.splitlines():
        if ':' not in line or line[:1].isspace():
            continue
        key, value = line.split(':', 1)
        values[key.strip()] = value.strip()
    return values, hashlib.sha256(raw).hexdigest()


def _list(value):
    value = value.strip()
    if value.startswith('[') and value.endswith(']'):
        value = value[1:-1]
    return [part.strip().strip('"\'「」') for part in value.split(',') if part.strip()]


FICTION_MARKER = re.compile(r'fiction|小說', re.I)


def _fiction_aliases(fields):
    aliases = set()
    for value in _list(fields.get('aka', '')):
        if FICTION_MARKER.search(value):
            alias = re.split(r'[（(]', value, maxsplit=1)[0].strip()
            aliases.update(p.strip() for p in re.split(r'[/／]', alias) if p.strip())
    return aliases


def _names(path, fields, override, forbidden_aliases=frozenset()):
    names = {path.stem}
    name = fields.get('name', path.stem)
    names.add(name)
    # Parenthetical names and slash-separated names are useful aliases, but the
    # private alias index is not itself shared with the room model.
    for group in re.findall(r'[（(]([^）)]+)[）)]', name):
        names.update(p.strip() for p in re.split(r'[/／]', group) if p.strip())
    names.add(re.sub(r'[（(].*$', '', name).strip())
    names.update(_list(fields.get('aka', '')))
    names.update(override.get('aliases', []))
    return {n for n in names if n and len(n) <= 80 and n not in forbidden_aliases
            and not BLOCKED.search(n) and not FICTION_MARKER.search(n)}


def _safe_relation(value):
    # Parentheses in this wiki tend to hold evidence, caveats or private detail.
    # The short room card only needs the outer relationship label.
    value = re.sub(r'[（(][^）)]*[）)]', '', value or '')
    kept = []
    for part in re.split(r'\s*(?:\+|→|/|／|；|;)\s*', value):
        # Relationship frontmatter sometimes continues into a miniature story.
        # Keep only its leading label; details require an explicit override.
        part = re.split(r'[,，、]|\s+[—–-]{2,}\s+', part, maxsplit=1)[0]
        part = part.replace('**', '').strip(' ,，。')
        if (part and len(part) <= 60 and SAFE_RELATION.search(part)
                and not BLOCKED.search(part)):
            kept.append(part)
    return '、'.join(kept[:3])[:180]


def _contains(text, alias):
    if alias.isascii():
        return re.search(r'(?<![A-Za-z0-9_])' + re.escape(alias) + r'(?![A-Za-z0-9_])', text, re.I) is not None
    return alias in text


class LifeContextResolver:
    """Pinned approval plus source-hash cache; policy changes require restart."""

    def __init__(self, policy_path, registry):
        self.path = Path(policy_path)
        self.policy, self.version = _private_json(self.path)
        self.registry = registry
        self._cache = {}
        self._validate_policy()

    def _validate_policy(self):
        p, r = self.policy, self.registry
        scope = p.get('intended_scope', {})
        if (p.get('version') != 1 or p.get('status') != 'approved'
                or p.get('approved_by') != r.bot_owners[r.self_id]
                or scope != {'guild_id': r.guild_id, 'channel_id': r.channel_id,
                             'human_ids': list(r.human_ids)}):
            raise NotReady('Life context policy is not approved for this audience')
        defaults = p.get('defaults', {})
        if set(defaults.get('allow', [])) != {
                'identity', 'aliases', 'basic_relationship', 'met_via',
                'common_circle', 'coarse_location'}:
            raise NotReady('Unexpected Life context default scope')
        required_denies = {'exact_address', 'live_location', 'health', 'intimacy',
                           'private_transcript', 'work_secret'}
        if not required_denies <= set(defaults.get('deny', [])):
            raise NotReady('Life context policy is missing mandatory denials')
        root = Path(p.get('life_wiki_root', ''))
        if root.is_symlink() or not root.is_dir() or not (root / 'people').is_dir():
            raise NotReady('Life Wiki root unavailable')
        self.root = root

    def validate(self):
        _, version = _private_json(self.path)
        if version != self.version:
            raise NotReady('Life context policy changed; restart for the new approval')

    def _index(self):
        raw_rows, rows, forbidden_aliases = [], [], set()
        overrides = self.policy.get('people_overrides', {})
        for path in sorted((self.root / 'people').glob('*.md')):
            fields, source_version = _frontmatter(path)
            forbidden_aliases.update(_fiction_aliases(fields))
            raw_rows.append((path, fields, source_version))
        for path, fields, source_version in raw_rows:
            canonical = path.stem
            override = overrides.get(canonical, {})
            rows.append((path, fields, source_version, override,
                         _names(path, fields, override, forbidden_aliases)))
        return rows

    def approved_records(self):
        """Build the searchable permission projection, never the raw wiki body.

        Selection belongs to the caller's explicit query, not recent chat. Keep
        the legacy resolver below for old callers; Party uses these records.
        """
        self.validate()
        records = []
        for path, fields, source_version, override, aliases in self._index():
            key = (self.version, source_version, path.name, 'record')
            if key not in self._cache:
                record = {'id': 'person-' + hashlib.sha256(path.name.encode()).hexdigest()[:20],
                          'kind': 'person', 'name': override.get('display_name', path.stem),
                          'aliases': sorted(aliases), 'source_version': source_version}
                for target, value in (
                    ('basic_relationship', override.get('safe_relationship') or _safe_relation(fields.get('relationship', ''))),
                    ('met_via', override.get('safe_met_via') or _safe_relation(fields.get('met_via', ''))),
                    ('common_circle', str(override.get('common_circle', ''))[:120]),
                ):
                    if value:
                        record[target] = value
                self._cache[key] = record
            records.append(dict(self._cache[key]))
        home = self.policy.get('home_context', {})
        if home.get('area'):
            records.append({'id': 'home-area', 'kind': 'place',
                            'name': home.get('label', '住家生活圈'),
                            'aliases': ['住家', '我家', '我們家', '家裡', '生活圈'],
                            'area': str(home['area'])[:120],
                            'precision': 'coarse; no address, coordinates or live presence',
                            'source_version': self.version})
        from .shared_context import read_projection
        shared, self.source_checks = read_projection(self)
        records.extend(shared)
        self.validate()  # A policy changed mid-build must not publish a snapshot.
        return records

    def resolve(self, context):
        self.validate()
        recent = '\n'.join(str(row.get('content', '')) for row in context[-8:])
        matches = {}
        for path, fields, source_version, override, aliases in self._index():
            hit = sorted((a for a in aliases if _contains(recent, a)), key=len, reverse=True)
            if hit:
                matches.setdefault(hit[0].casefold(), []).append(
                    (path, fields, source_version, override, hit[0]))
        cards = []
        for alias, candidates in sorted(matches.items()):
            if len(candidates) > 1:
                cards.append({'kind': 'ambiguous_person', 'mentioned_as': candidates[0][4],
                              'candidates': sorted({c[0].stem for c in candidates})[:5]})
                continue
            path, fields, source_version, override, hit = candidates[0]
            key = (self.version, source_version, path.name)
            if key not in self._cache:
                relation = override.get('safe_relationship') or _safe_relation(fields.get('relationship', ''))
                met_via = override.get('safe_met_via') or _safe_relation(fields.get('met_via', ''))
                card = {'kind': 'person', 'name': override.get('display_name', path.stem),
                        'mentioned_as': hit, 'source_version': source_version}
                if relation:
                    card['basic_relationship'] = relation
                if met_via:
                    card['met_via'] = met_via
                if override.get('common_circle'):
                    card['common_circle'] = str(override['common_circle'])[:120]
                self._cache[key] = card
            cards.append(dict(self._cache[key], mentioned_as=hit))
        home = self.policy.get('home_context', {})
        if HOME_CUES.search(recent) and home.get('area'):
            cards.append({'kind': 'coarse_location', 'label': home.get('label', '住家生活圈'),
                          'area': str(home['area'])[:120],
                          'precision': 'coarse; no address, coordinates or live presence'})
        return cards[:6]
