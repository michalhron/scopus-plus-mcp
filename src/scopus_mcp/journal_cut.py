"""A quality cut-off that is relative to each journal's own field.

CiteScore ranks every journal inside each Scopus subject category (ASJC) it is
listed in, so a percentile compares a marketing journal with marketing
journals and an IS journal with IS journals. The cut keeps the journals of a
corpus at or above `min_percentile` in their category, which lets one corpus
span fields with different citation norms.

Steps (request costs in brackets):
1. venues of the corpus: one grouped request on the corpus filter [1 per 200
   venues];
2. their ISSNs from OpenAlex [1 per 50 venues];
3. CiteScore percentiles per category from the Scopus Serial Title API
   [1 per SERIAL_BATCH ISSNs];
4. a source-ID filter for the kept venues, run in chunks of OR_CHUNK.

Venues are listed, never silently dropped or kept: those below the cut, those
with no CiteScore (not in Scopus, no complete CiteScore year, proceedings and
book series) and those kept by `include_venues`.

OpenAlex's own journal measures are not used as a fallback. Mean citedness
differs by field by an order of magnitude, and a fair percentile would need
every source of each journal's field, thousands of requests. Without Scopus
Serial Title access the cut refuses. A cut that keeps no venue raises.
"""
import asyncio
import calendar
import math
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional, Tuple

from .client import SERIAL_BATCH
from .journals import (
    category_names, normalize_issn, format_issn, resolve_categories, serial_entry_issns,
    subject_ranks, venue_type,
)
from .openalex import FilterParts, ID_BATCH, PER_PAGE, chunk_or, short_id

MAX_VENUES = 2000
_SOURCE_ID = re.compile(r'^(?:https?://openalex\.org/)?(S\d+)$', re.IGNORECASE)

QUALITY_PROPS = {
    "min_percentile": {
        "type": "integer", "minimum": 0, "maximum": 99,
        "description": (
            "OpenAlex only. Quality cut-off, relative to each journal's own "
            "field: keep papers from journals whose CiteScore percentile in "
            "their Scopus subject category is at least this (75 = top "
            "quartile). A marketing journal is compared with marketing "
            "journals, an IS journal with IS journals, so one corpus can span "
            "fields. 0 keeps every journal Scopus indexes and nothing else, a "
            "floor that removes most junk venues. Needs Scopus Serial Title "
            "access; without it the cut refuses, as OpenAlex's own measures "
            "cannot be compared across fields. Cost: 1 OpenAlex request per "
            "200 venues, 1 per 50 for their ISSNs, 1 Scopus request per 25 "
            "ISSNs, then the search itself in chunks of 50 journals. Journals "
            "kept, dropped and without CiteScore are listed in the output and "
            "a CSV."
        ),
    },
    "percentile_rule": {
        "type": "string", "enum": ["best", "all"], "default": "best",
        "description": (
            "Which category decides when a journal sits in several. 'best' "
            "(default): its highest percentile, generous to multidisciplinary "
            "journals. 'all': it must clear the bar in every category it is "
            "listed in."
        ),
    },
    "categories": {
        "type": "array", "items": {"type": "string"},
        "description": (
            "Only these Scopus (ASJC) categories count toward the percentile: "
            "4-digit codes or category names. A journal ranked in none of "
            "them is dropped. Leave out to count all of a journal's categories."
        ),
    },
    "include_venues": {
        "type": "array", "items": {"type": "string"},
        "description": (
            "Venues kept whatever their percentile: ISSNs or OpenAlex source "
            "IDs (S...). Use for conference proceedings such as ICIS, ECIS or "
            "HICSS, which have no journal CiteScore and are otherwise dropped."
        ),
    },
}


@dataclass
class CutReport:
    min_percentile: int
    percentile_rule: str
    categories: List[str]
    citescore_year: Optional[int] = None
    works_in_corpus: int = 0
    works_without_venue: int = 0
    kept: List[Dict[str, Any]] = dc_field(default_factory=list)
    dropped: List[Dict[str, Any]] = dc_field(default_factory=list)
    no_citescore: List[Dict[str, Any]] = dc_field(default_factory=list)
    included: List[Dict[str, Any]] = dc_field(default_factory=list)
    requests: Dict[str, int] = dc_field(default_factory=dict)
    rows: List[Dict[str, Any]] = dc_field(default_factory=list)
    csv_path: Optional[str] = None

    def _works(self, rows):
        return sum(r['works'] for r in rows)

    @property
    def works_kept(self) -> int:
        return self._works(self.kept) + self._works(self.included)

    @property
    def share_removed(self) -> Optional[float]:
        if not self.works_in_corpus:
            return None
        return round(1 - self.works_kept / self.works_in_corpus, 3)

    @property
    def proceedings_dropped(self) -> Dict[str, int]:
        rows = [r for r in self.no_citescore if r.get('venue_type') in
                ('conference proceedings', 'book series', 'conference')]
        return {'venues': len(rows), 'works': self._works(rows)}

    def as_dict(self) -> Dict[str, Any]:
        return {
            'min_percentile': self.min_percentile,
            'percentile_rule': self.percentile_rule,
            'categories': self.categories or 'all',
            'citescore_year': self.citescore_year,
            'works_in_corpus_before_cut': self.works_in_corpus,
            'works_kept': self.works_kept,
            'share_removed': self.share_removed,
            'works_without_venue': self.works_without_venue,
            'journals_kept': len(self.kept), 'journals_below_cut': len(self.dropped),
            'venues_without_citescore': len(self.no_citescore),
            'venues_included': len(self.included),
            'proceedings_dropped': self.proceedings_dropped,
            'requests': self.requests,
            'csv_path': self.csv_path,
            'kept': self.kept, 'dropped': self.dropped,
            'no_citescore': self.no_citescore, 'included': self.included,
        }

    def report(self, top: int = 8) -> str:
        pct = self.share_removed
        scope = (f"{self.percentile_rule} category percentile >= {self.min_percentile}"
                 if self.min_percentile else "indexed in Scopus (min_percentile 0)")
        lines = [
            f"Quality cut ({scope}; CiteScore {self.citescore_year or 'n/a'}): "
            f"{len(self.kept)} journals kept, {len(self.dropped)} below the cut, "
            f"{len(self.no_citescore)} venues with no CiteScore, "
            f"{len(self.included)} kept by include_venues. "
            f"{self.works_kept} of {self.works_in_corpus} papers kept"
            + (f" ({round(100 * pct)}% removed)." if pct is not None else ".")
        ]
        if self.works_without_venue:
            lines.append(f"{self.works_without_venue} papers have no venue in OpenAlex and are cut.")
        pd = self.proceedings_dropped
        if pd['venues']:
            lines.append(f"Proceedings or book series dropped: {pd['venues']} venues, "
                         f"{pd['works']} papers. Add them to include_venues to keep them.")
        if self.no_citescore:
            lines.append("No CiteScore (largest first): " + '; '.join(
                f"{r['title']} ({r['works']}; {r['why']})"
                for r in self.no_citescore[:top]) + ('; ...' if len(self.no_citescore) > top else ''))
        if self.dropped:
            lines.append("Below the cut (largest first): " + '; '.join(
                f"{r['title']} ({r['works']}; {r['percentile']}th in {r['category']})"
                for r in self.dropped[:top]) + ('; ...' if len(self.dropped) > top else ''))
        if self.csv_path:
            lines.append(f"Every venue with its status: {self.csv_path}")
        return '\n'.join(lines)


def parse_include(values: Optional[List[str]]) -> Tuple[set, set]:
    """(OpenAlex source IDs, compact ISSNs) from include_venues."""
    ids, issns = set(), set()
    for v in values or []:
        text = str(v).strip()
        m = _SOURCE_ID.match(text)
        if m:
            ids.add(m.group(1).upper())
        elif normalize_issn(text):
            issns.add(normalize_issn(text))
        else:
            raise ValueError(f"include_venues entry {text!r} is neither an ISSN nor an "
                             "OpenAlex source ID (S...).")
    return ids, issns


VENUE_KEY = 'primary_location.source.id'
SPLIT_PAUSE = 0.2  # seconds between split requests, to stay polite to OpenAlex


def _saturated(groups, total: int) -> bool:
    """A grouped reply is cut off when it holds the 200-group maximum and the
    groups do not add up to all works (works with no venue show up as one
    group, so a complete reply can still be short of the total)."""
    return (len(groups) >= PER_PAGE
            and sum(int(g.get('count') or 0) for g in groups) < total)


async def _venue_groups(openalex, f: str) -> Tuple[List[Dict[str, Any]], int, int]:
    """(venue groups, total works, requests). OpenAlex returns at most the 200
    largest venues per request, so a saturated reply is split by publication
    year, and a still saturated year by month, and the counts are added up."""
    groups, n = await openalex.group_by(f, VENUE_KEY, max_groups=MAX_VENUES)
    calls = 1
    if not _saturated(groups, n):
        return groups, n, calls
    merged: Dict[str, Dict[str, Any]] = {}

    def add(gs):
        for g in gs:
            key = str(g.get('key'))
            m = merged.setdefault(key, {'key': g.get('key'),
                                        'key_display_name': g.get('key_display_name'),
                                        'count': 0})
            m['count'] += int(g.get('count') or 0)

    years, _ = await openalex.group_by(f, 'publication_year')
    calls += 1
    for yg in sorted(years, key=lambda g: str(g.get('key'))):
        year = str(yg.get('key'))
        if not year.isdigit() or not int(yg.get('count') or 0):
            continue
        await asyncio.sleep(SPLIT_PAUSE)
        yf = f"{f},publication_year:{year}"
        ygroups, yn = await openalex.group_by(yf, VENUE_KEY, max_groups=MAX_VENUES)
        calls += 1
        if not _saturated(ygroups, yn):
            add(ygroups)
            continue
        for month in range(1, 13):
            await asyncio.sleep(SPLIT_PAUSE)
            last = calendar.monthrange(int(year), month)[1]
            mf = (f"{f},from_publication_date:{year}-{month:02d}-01,"
                  f"to_publication_date:{year}-{month:02d}-{last:02d}")
            mgroups, _mn = await openalex.group_by(mf, VENUE_KEY, max_groups=MAX_VENUES)
            calls += 1
            add(mgroups)
    return list(merged.values()), n, calls


async def _venues(openalex, filter_expr: FilterParts) -> Tuple[List[Dict[str, Any]], int, int, int]:
    """(venues with work counts, total works, works without a venue, requests).
    A list of filters (chunks of a scope) is grouped chunk by chunk and the
    counts added up."""
    by_id: Dict[str, Dict[str, Any]] = {}
    total = unknown = calls = 0
    for f in ([filter_expr] if isinstance(filter_expr, str) else list(filter_expr or [])):
        groups, n, used = await _venue_groups(openalex, f)
        calls += used
        total += n
        seen = 0
        for g in groups:
            seen += int(g.get('count') or 0)
            sid = short_id(str(g.get('key')))
            if not sid or not _SOURCE_ID.match(sid):
                unknown += int(g.get('count') or 0)
                continue
            v = by_id.setdefault(sid, {'openalex_id': sid, 'title': g.get('key_display_name'),
                                       'works': 0})
            v['works'] += int(g.get('count') or 0)
        unknown += max(0, n - seen)
    return list(by_id.values()), total, unknown, calls


def _decide(entry: Optional[Dict[str, Any]], names, wanted: set, min_pct: int,
            rule: str) -> Dict[str, Any]:
    """Verdict for one venue given its Serial Title entry."""
    if entry is None:
        return {'status': 'no_citescore', 'why': 'not found in Scopus'}
    vtype = venue_type(entry.get('prism:aggregationType'))
    if vtype != 'journal':
        return {'status': 'no_citescore', 'why': f'Scopus lists it as {vtype}',
                'venue_type': vtype}
    ranks = subject_ranks(entry, names)
    considered = [r for r in ranks['ranks'] if not wanted or r['code'] in wanted]
    if not min_pct:
        return {'status': 'kept', 'percentile': considered[0]['percentile'] if considered else None,
                'category': considered[0]['category'] if considered else None,
                'citescore_year': ranks['year'], 'venue_type': vtype}
    if not ranks['ranks']:
        return {'status': 'no_citescore', 'why': 'no complete CiteScore year', 'venue_type': vtype}
    if not considered:
        return {'status': 'dropped', 'percentile': None, 'category': None, 'venue_type': vtype,
                'why': 'ranked in none of the chosen categories', 'citescore_year': ranks['year']}
    pick = max(considered, key=lambda r: r['percentile']) if rule == 'best' else \
        min(considered, key=lambda r: r['percentile'])
    return {'status': 'kept' if pick['percentile'] >= min_pct else 'dropped',
            'percentile': pick['percentile'], 'category': pick['category'],
            'citescore_year': ranks['year'], 'venue_type': vtype}


async def apply_quality_cut(openalex, scopus, corpus_filter: FilterParts, arguments: dict
                            ) -> Tuple[FilterParts, CutReport]:
    """Source-ID filter for the venues that clear the bar, and the report.

    corpus_filter is the full OpenAlex filter of the corpus before the cut
    (query, topics, scope, years). Raises ValueError for bad arguments and
    when Scopus Serial Title access is missing.
    """
    min_pct = int(arguments["min_percentile"])
    if not 0 <= min_pct <= 99:
        raise ValueError("min_percentile must be between 0 and 99")
    rule = (arguments.get("percentile_rule") or 'best').lower()
    if rule not in ('best', 'all'):
        raise ValueError("percentile_rule must be 'best' or 'all'")
    include_ids, include_issns = parse_include(arguments.get("include_venues"))

    names, wanted, chosen = {}, set(), []
    if arguments.get("categories"):
        asjc = await scopus.asjc_categories()
        names = category_names(asjc)
        chosen = resolve_categories([str(c) for c in arguments["categories"]], asjc)
        wanted = {c['code'] for c in chosen}

    venues, total, unknown, group_calls = await _venues(openalex, corpus_filter)
    report = CutReport(min_percentile=min_pct, percentile_rule=rule,
                       categories=[c['category'] for c in chosen],
                       works_in_corpus=total, works_without_venue=unknown)
    if not venues:
        raise ValueError("No papers match the corpus filter before the quality cut, "
                         "so there is nothing to cut.")

    sources = {}
    for i in range(0, len(venues), ID_BATCH):
        for s in await openalex.sources_by_ids([v['openalex_id'] for v in venues[i:i + ID_BATCH]]):
            sources[short_id(s.get('id'))] = s
    issn_of = {}
    for v in venues:
        s = sources.get(v['openalex_id']) or {}
        issn_of[v['openalex_id']] = [c for c in (normalize_issn(x) for x in (s.get('issn') or [])) if c]
    candidates = sorted({c for cs in issn_of.values() for c in cs})

    try:
        entries = await scopus.serial_titles(candidates) if candidates else []
    except Exception as exc:
        raise ValueError(
            "The quality cut needs the Scopus Serial Title API (CiteScore percentiles "
            f"per category), which failed: {exc}. OpenAlex's own journal measures are "
            "not field-relative, so there is no fallback. Options: connect to a network "
            "with Scopus access, or pass a journal list you trust as scope (ISSNs or a "
            "CSV path) instead of min_percentile.") from None
    by_issn = {}
    for e in entries:
        for c in serial_entry_issns(e):
            by_issn.setdefault(c, e)

    rows = []
    for v in venues:
        s = sources.get(v['openalex_id']) or {}
        issns = issn_of[v['openalex_id']]
        entry = next((by_issn[c] for c in issns if c in by_issn), None)
        row = {'title': s.get('display_name') or v['title'], 'openalex_id': v['openalex_id'],
               'issn': format_issn(normalize_issn(s.get('issn_l')) or (issns[0] if issns else None)),
               'works': v['works'], 'openalex_type': s.get('type')}
        verdict = _decide(entry, names, wanted, min_pct, rule)
        row.update(verdict)
        if not entry and not issns:
            row['why'] = 'no ISSN in OpenAlex'
        if row['status'] == 'no_citescore' and s.get('type') in ('conference', 'book series'):
            row.setdefault('venue_type', 'conference proceedings' if s.get('type') == 'conference'
                           else 'book series')
        included = v['openalex_id'] in include_ids or bool(include_issns & set(issns))
        if included:
            row['reason'] = f"include_venues (was {row['status']})"
            row['status'] = 'included'
        rows.append(row)
        if verdict.get('citescore_year'):
            report.citescore_year = max(report.citescore_year or 0, verdict['citescore_year'])
    for status, bucket in (('kept', report.kept), ('dropped', report.dropped),
                           ('no_citescore', report.no_citescore), ('included', report.included)):
        bucket.extend(sorted((r for r in rows if r['status'] == status), key=lambda r: -r['works']))
    report.requests = {
        'openalex_group_by': group_calls,
        'openalex_source_lookups': math.ceil(len(venues) / ID_BATCH),
        'scopus_serial_title': math.ceil(len(candidates) / SERIAL_BATCH) if candidates else 0,
    }
    kept_ids = [r['openalex_id'] for r in report.kept + report.included]
    report.rows = rows
    if not kept_ids:
        raise ValueError("No venue clears the quality cut, so the corpus would be empty.\n"
                         + report.report())
    return chunk_or('primary_location.source.id', kept_ids), report
