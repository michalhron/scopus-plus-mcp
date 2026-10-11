"""OpenAlex topics as a corpus scope.

OpenAlex files every work under a topic, which sits in a subfield, a field and
a domain (4,516 topics, 252 subfields, 26 fields, 4 domains). A scope names
topics, subfields or fields by ID or exact name and becomes a filter on the
work's primary topic (the one the classifier ranks first) or on any of its
topics.

Names are matched exactly. A wrong scope silently corrupts a corpus, so an
ambiguous or unknown name raises with the candidates instead of guessing, the
rule `find_by_title` follows.
"""
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional

from .openalex import FilterParts, chunk_or, short_id

# Level -> filter path below `primary_topic.` / `topics.`.
LEVEL_KEYS = {'topic': 'id', 'subfield': 'subfield.id', 'field': 'field.id'}
LEVEL_ENDPOINTS = {'topic': 'topics', 'subfield': 'subfields', 'field': 'fields'}

# Records that are papers or books. The October 2026 classifier also assigns
# topics to datasets, specimens and proposals, so counts need a type filter.
DEFAULT_WORK_TYPES = ('article', 'review', 'book', 'book-chapter')
MAX_TOPICS = 50  # one OR filter, never chunked, so counts stay exact

_TOPIC_ID = re.compile(r'^(?:https?://openalex\.org/)?(T\d+)$', re.IGNORECASE)
_SUBFIELD_ID = re.compile(r'^(?:https?://openalex\.org/)?subfields?[/:]\s*(\d+)$', re.IGNORECASE)
_FIELD_ID = re.compile(r'^(?:https?://openalex\.org/)?fields?[/:]\s*(\d+)$', re.IGNORECASE)

TOPIC_PROPS = {
    "topics": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "OpenAlex only. Keep papers about these topics, subfields or fields: "
            "topic IDs ('T10346'), 'subfield:1710' or 'field:17' (or "
            "'subfields/1710', 'fields/17'), or exact names ('Digital "
            "Transformation'). Several entries combine as OR, all at one level. "
            "A name that matches more than one entity, or none exactly, is "
            "refused with the candidates listed. Each name costs one extra "
            "request (the 26 fields and 252 subfields are fetched once and "
            "cached). Combines with scope and quality as AND. Not available "
            "with source='scopus'."
        ),
    },
    "topic_match": {
        "type": "string",
        "enum": ["primary", "any"],
        "default": "primary",
        "description": (
            "'primary' (default) matches the one topic OpenAlex ranks first for "
            "a paper: a tighter corpus. 'any' matches a paper when any of its "
            "assigned topics (up to three) is in the scope: wider, and it "
            "catches papers that sit between fields."
        ),
    },
    "work_types": {
        "description": (
            "With topics: the OpenAlex record types to keep. Default "
            "['article', 'review', 'book', 'book-chapter'], which leaves out "
            "datasets, specimens, proposals and other records that now receive "
            "topics. 'all' keeps every type."
        ),
        "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
    },
}


@dataclass
class TopicScope:
    entities: List[Dict[str, str]]       # {'id', 'name', 'level'}
    match: str
    work_types: Optional[List[str]]      # None = all types
    topic_filter: str = ''
    type_filter: Optional[str] = None
    notes: List[str] = dc_field(default_factory=list)

    @property
    def level(self) -> str:
        return self.entities[0]['level']

    @property
    def filter(self) -> FilterParts:
        from .openalex import and_filters
        return and_filters(self.topic_filter, self.type_filter)

    def as_dict(self) -> Dict[str, Any]:
        return {
            'level': self.level,
            'match': self.match,
            'topics': [{'id': e['id'], 'name': e['name']} for e in self.entities],
            'work_types': self.work_types or 'all',
            'openalex_filter': ','.join(f for f in (self.topic_filter, self.type_filter) if f),
        }

    def report(self) -> str:
        names = '; '.join(f"{e['name']} ({e['id']})" for e in self.entities)
        where = 'primary topic' if self.match == 'primary' else 'any assigned topic'
        types = ', '.join(self.work_types) if self.work_types else 'all types'
        return (f"Topic scope: {self.level}{'s' if len(self.entities) > 1 else ''} {names}; "
                f"matched on {where}; record types: {types}.")


def reject_topics_for_scopus(arguments: dict) -> None:
    if any(arguments.get(k) is not None and arguments.get(k) != [] for k in (
            'topics', 'work_types', 'min_percentile', 'percentile_rule', 'include_venues')):
        raise ValueError(
            "topics, work_types and the quality cut (min_percentile) need "
            "source='openalex'. Scopus has subject areas (ASJC), not OpenAlex "
            "topics, and the two do not map onto each other. For a Scopus "
            "quality bar use find_journals and pass its ISSNs as scope."
        )


def _norm(text: str) -> str:
    return ' '.join(re.sub(r'[^0-9a-z]+', ' ', (text or '').lower()).split())


def _candidate(level: str, node: Dict[str, Any]) -> Dict[str, str]:
    return {'id': short_id(node.get('id')) or '', 'name': node.get('display_name') or '',
            'level': level}


def _describe(c: Dict[str, str]) -> str:
    ref = c['id'] if c['level'] == 'topic' else f"{c['level']}:{c['id']}"
    return f"{c['name']} [{c['level']} {ref}]"


async def _explicit(openalex, text: str) -> Optional[Dict[str, str]]:
    """Entity for an ID-form input, or None when `text` is a name."""
    m = _TOPIC_ID.match(text)
    if m:
        tid = m.group(1).upper()
        node = await openalex.get_entity('topics', tid)
        if not node:
            raise ValueError(f"No OpenAlex topic {tid}.")
        return _candidate('topic', node)
    for pattern, level in ((_SUBFIELD_ID, 'subfield'), (_FIELD_ID, 'field')):
        m = pattern.match(text)
        if m:
            wanted = m.group(1)
            for node in await openalex.taxonomy(LEVEL_ENDPOINTS[level]):
                if short_id(node.get('id')) == wanted:
                    return _candidate(level, node)
            raise ValueError(f"No OpenAlex {level} {wanted}.")
    return None


async def resolve_topics(openalex, inputs: List[str]) -> List[Dict[str, str]]:
    """Entities ({'id', 'name', 'level'}) for IDs or exact names, one level.

    Request cost: the 26 fields and 252 subfields are fetched once and cached
    (3 list requests, 1 credit each); each name adds one topic search
    (1 credit); each topic ID is a free single lookup."""
    inputs = [str(i).strip() for i in inputs if str(i).strip()]
    if not inputs:
        raise ValueError("topics is empty.")
    if len(inputs) > MAX_TOPICS:
        raise ValueError(f"At most {MAX_TOPICS} topics per call.")
    resolved: List[Dict[str, str]] = []
    levels_pool: List[Dict[str, str]] = []
    for text in inputs:
        hit = await _explicit(openalex, text)
        if hit is None:
            wanted = _norm(text)
            if not levels_pool:
                levels_pool = [_candidate('field', n) for n in await openalex.taxonomy('fields')]
                levels_pool += [_candidate('subfield', n) for n in await openalex.taxonomy('subfields')]
            pool = levels_pool + [_candidate('topic', n) for n in await openalex.search_topics(text)]
            exact = [c for c in pool if _norm(c['name']) == wanted]
            if len(exact) > 1:
                raise ValueError(
                    f"{text!r} matches several entities; pass an ID instead: "
                    + '; '.join(_describe(c) for c in exact))
            if not exact:
                near = [c for c in pool if wanted and wanted in _norm(c['name'])][:10]
                hint = (" Close matches: " + '; '.join(_describe(c) for c in near)) if near else ''
                raise ValueError(f"No topic, subfield or field is named exactly {text!r}.{hint}")
            hit = exact[0]
        if hit not in resolved:
            resolved.append(hit)
    levels = {c['level'] for c in resolved}
    if len(levels) > 1:
        raise ValueError(
            "topics mixes levels (" + ', '.join(sorted(levels)) + "). OpenAlex "
            "filters one level at a time; run one call per level, or list only "
            "topics, only subfields or only fields.")
    return resolved


def topic_filter(entities: List[Dict[str, str]], match: str) -> str:
    prefix = 'primary_topic' if match == 'primary' else 'topics'
    key = f"{prefix}.{LEVEL_KEYS[entities[0]['level']]}"
    return chunk_or(key, [e['id'] for e in entities], size=MAX_TOPICS)  # type: ignore[return-value]


def parse_work_types(value: Any) -> Optional[List[str]]:
    """Types to keep; None for 'all'; the default when unset."""
    if value in (None, '', []):
        return list(DEFAULT_WORK_TYPES)
    items = [t for t in re.split(r'[,|\s]+', value) if t] if isinstance(value, str) else \
        [str(t).strip() for t in value if str(t).strip()]
    if any(t.lower() == 'all' for t in items):
        return None
    return [t.lower() for t in dict.fromkeys(items)]


async def build_topic_scope(openalex, arguments: dict,
                            types_alone: bool = False) -> Optional[TopicScope]:
    """TopicScope from tool arguments; None when no topics were given.
    types_alone lets work_types stand without topics (the caller applies it)."""
    inputs = arguments.get("topics")
    if isinstance(inputs, str):
        inputs = [t for t in re.split(r'\s*;\s*|\s*\|\s*', inputs) if t]
    if not inputs:
        if arguments.get("work_types") and not types_alone:
            raise ValueError("work_types applies together with topics.")
        return None
    match = (arguments.get("topic_match") or 'primary').lower()
    if match not in ('primary', 'any'):
        raise ValueError("topic_match must be 'primary' or 'any'.")
    entities = await resolve_topics(openalex, inputs)
    types = parse_work_types(arguments.get("work_types"))
    return TopicScope(
        entities=entities, match=match, work_types=types,
        topic_filter=topic_filter(entities, match),
        type_filter=('type:' + '|'.join(types)) if types else None,
    )
