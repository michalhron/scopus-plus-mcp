"""Topic profile: which fields, subfields and topics carry a debate."""
import csv
import json
import re
from datetime import datetime

import mcp.types as types

from .. import jobs
from ..baskets import SCOPE_SCHEMA, resolve_scope
from ..journal_cut import QUALITY_PROPS
from ..openalex import _search_value, and_filters
from ..output import _output_dir, _query_slug
from ..topic_profile import field_by_year, first_year_over, ranked
from ..topics import TOPIC_PROPS
from .common import resolve_openalex_scope, server_module

MAX_YEARS = 40

TOOLS = [
    types.Tool(
        name="topic_profile",
        description=(
            "Which disciplines carry a debate, and when each joined it. For an "
            "OpenAlex title-and-abstract query, optionally inside OpenAlex "
            "topics and a field-relative journal quality cut, reports papers "
            "and shares by field, subfield and topic (top N), and per year by "
            "field with the first year each field passed a share threshold. "
            "Counts papers, reviews, books and book chapters by default (the "
            "October 2026 classifier also gives datasets and specimens topics). "
            "Uses grouped requests, never one per paper: 4 plus one per year "
            "in the range (each multiplied by the number of journal chunks "
            "when a quality cut keeps over 50 journals), plus the requests "
            "the topic and quality options list. Topics follow the classifier "
            "that OpenAlex switched to on 6 October 2026."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Plain words matched against title and abstract; quote phrases.",
                },
                "from_year": {"type": "integer", "description": "First publication year."},
                "to_year": {"type": "integer", "description": "Last publication year."},
                "scope": SCOPE_SCHEMA,
                **TOPIC_PROPS,
                **QUALITY_PROPS,
                "top": {"type": "integer", "default": 20,
                        "description": "Fields, subfields and topics to list (largest first)."},
                "share_threshold": {"type": "number", "default": 0.05,
                                    "description": ("A field joins the debate in the first year it "
                                                    "makes up at least this share of that year's papers.")},
            },
            "required": ["query"],
        },
    ),
]


async def _topic_profile(arguments: dict) -> list:
    srv = server_module()
    openalex = srv.openalex
    query = (arguments.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    if arguments.get("source") not in (None, '', 'openalex'):
        raise ValueError("topic_profile runs on OpenAlex only.")
    lo, hi = arguments.get("from_year"), arguments.get("to_year")
    lo = int(lo) if lo is not None else None
    hi = int(hi) if hi is not None else None
    if lo and hi and lo > hi:
        raise ValueError(f"from_year {lo} is after to_year {hi}")
    top = max(1, min(int(arguments.get("top") or 20), 200))
    threshold = float(arguments.get("share_threshold") or 0.05)
    years_filter = f"publication_year:{lo or ''}-{hi or ''}" if (lo or hi) else None

    issns = resolve_scope(arguments.get("scope"))
    scope = await resolve_openalex_scope(srv, arguments, query, issns,
                                         base_extra=years_filter, default_types=True)
    search_filter = f'title_and_abstract.search:{_search_value(query)}'
    base = and_filters(search_filter, years_filter, scope.extra_filter)

    requests = 0
    jobs.progress("topic_profile: years")
    year_groups, total, n = await openalex.grouped_counts(base, 'publication_year')
    requests += n
    if not total:
        raise ValueError("No papers match the query, scope and record types.\n" + scope.summary)
    year_totals = {int(y): g['count'] for y, g in year_groups.items() if y.isdigit()}
    if len(year_totals) > MAX_YEARS:
        raise ValueError(f"The corpus spans {len(year_totals)} years; pass from_year to keep "
                         f"it within {MAX_YEARS}.")
    level = {}
    for name, key in (('fields', 'primary_topic.field.id'),
                      ('subfields', 'primary_topic.subfield.id'),
                      ('topics', 'primary_topic.id')):
        jobs.progress(f"topic_profile: {name}")
        groups, _, n = await openalex.grouped_counts(base, key)
        requests += n
        level[name] = ranked(groups, total, top)
    per_year = {}
    for i, year in enumerate(sorted(year_totals), 1):
        jobs.progress(f"topic_profile: fields in {year} ({i}/{len(year_totals)})")
        # One year replaces the range: a filter key is never repeated.
        groups, _, n = await openalex.grouped_counts(
            and_filters(search_filter, f'publication_year:{year}', scope.extra_filter),
            'primary_topic.field.id')
        requests += n
        per_year[year] = groups
    matrix = field_by_year(per_year, year_totals)
    joined = {fid: first_year_over(f['shares'], threshold) for fid, f in matrix['fields'].items()}

    slug = _query_slug(query)
    ts = datetime.now().strftime('%Y%m%dT%H%M%S')
    out = _output_dir()
    json_path = out / f'topic-profile-{slug}-{ts}.json'
    csv_path = out / f'topic-profile-{slug}-{ts}-field-by-year.csv'
    result = {
        'query': query, 'years': [lo, hi], 'total_papers': total,
        'scope': scope.info, 'fields': level['fields'], 'subfields': level['subfields'],
        'topics': level['topics'],
        'field_by_year': {'years': matrix['years'], 'fields': {
            fid: {**f, 'first_year_over_threshold': joined[fid]} for fid, f in matrix['fields'].items()}},
        'share_threshold': threshold, 'requests': requests,
    }
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['field_id', 'field', 'year', 'papers', 'share_of_year'])
        for fid, fld in matrix['fields'].items():
            for year in matrix['years']:
                if year in fld['counts']:
                    w.writerow([fid, fld['name'], year, fld['counts'][year], fld['shares'][year]])

    lines = list(scope.lines)
    lines.append(f"Topic profile of {total} papers for {query!r}"
                 + (f", {lo or ''}-{hi or ''}" if years_filter else "")
                 + f" ({requests} OpenAlex requests). Shares are of all {total} papers.")
    for title, key in (('Fields', 'fields'), ('Subfields', 'subfields'), ('Topics', 'topics')):
        block = level[key]
        lines.append(f"{title} (top {len(block['rows'])} of {block['distinct']}; "
                     f"{block['unclassified']} papers unclassified):")
        for r in block['rows']:
            lines.append(f"  {r['n']:>6}  {100 * r['share']:5.1f}%  {r['name']}")
    lines.append(f"First year each field made up at least {round(100 * threshold)}% of that "
                 "year's papers:")
    ordered = sorted(matrix['fields'].items(), key=lambda kv: -sum(kv[1]['counts'].values()))
    for fid, fld in ordered[:top]:
        year = joined[fid]
        lines.append(f"  {fld['name']}: " + (str(year) if year else "never"))
    lines += [f"JSON: {json_path}", f"Field by year CSV: {csv_path}"]
    return [types.TextContent(type="text", text='\n'.join(lines))]


HANDLERS = {'topic_profile': _topic_profile}
