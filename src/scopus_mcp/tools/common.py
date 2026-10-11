"""Shared tool helpers: source switch, OpenAlex resolution, CSV output."""
from dataclasses import dataclass, field
from datetime import datetime as _today_datetime
from typing import Any, Dict, List, Optional

from ..baskets import openalex_issn_filter
from ..journal_cut import apply_quality_cut
from ..openalex import FilterParts, _search_value, and_filters, openalex_work_key
from ..topics import build_topic_scope, parse_work_types
from ..output import _output_dir
from ..records import clean_abstract_details, to_scopus_id


def server_module():
    """The running server module. Tools read client, openalex and the
    patchable helpers from it at call time, so one module owns them."""
    from .. import server
    return server


SOURCE_SCHEMA = {
    "type": "string",
    "enum": ["scopus", "openalex"],
    "default": "scopus",
    "description": (
        "Data source. 'scopus' (default) needs subscriber entitlement for "
        "search, citations and references. 'openalex' needs none: IDs may be "
        "DOIs, OpenAlex work IDs (W...), or Scopus IDs (resolved to a DOI via "
        "Scopus metadata), and results carry OpenAlex IDs. Never mix sources "
        "within one analysis."
    ),
}


def _source(arguments: dict) -> str:
    source = (arguments.get("source") or "scopus").lower()
    if source not in ("scopus", "openalex"):
        raise ValueError(f"source must be 'scopus' or 'openalex', not {source!r}")
    return source


async def _resolve_openalex_work(identifier: str) -> dict:
    """Raw OpenAlex work for a DOI, OpenAlex ID, or Scopus ID/EID.

    Scopus IDs go through Scopus metadata to get a DOI; that lookup needs no
    subscriber entitlement, so it keeps working off campus. Records without
    a DOI (common for AIS conference papers) fall back to an exact title
    match within one year.
    """
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    identifier = str(identifier).strip()
    if openalex_work_key(identifier) is None:
        details = clean_abstract_details(await client.get_abstract(to_scopus_id(identifier)))
        doi = details.get('doi')
        if not doi:
            title = details.get('title')
            year = (details.get('cover_date') or '')[:4]
            work = await openalex.find_by_title(title, int(year) if year.isdigit() else None)
            if not work:
                raise ValueError(
                    f"Scopus record {identifier} has no DOI and no exact title match "
                    f"in OpenAlex ({title!r}); pass a DOI or OpenAlex work ID instead."
                )
            return work
        identifier = doi
    work = await openalex.get_work(identifier)
    if not work:
        raise ValueError(f"OpenAlex has no work for {identifier!r}.")
    return work


def _flatten_row(row: dict) -> dict:
    """CSV-friendly: {'year', 'value'} metrics become value + _year columns,
    lists become '; '-joined strings."""
    flat = {}
    for k, v in row.items():
        if isinstance(v, dict) and set(v) == {'year', 'value'}:
            flat[k] = v['value']
            flat[f'{k}_year'] = v['year']
        elif isinstance(v, list):
            flat[k] = '; '.join(str(x) for x in v)
        else:
            flat[k] = v
    return flat


def _write_rows_csv(rows: list, prefix: str):
    import csv
    flat = [_flatten_row(r) for r in rows]
    columns = []
    for r in flat:
        columns.extend(k for k in r if k not in columns)
    ts = _today_datetime.now().strftime('%Y%m%dT%H%M%S')
    path = _output_dir() / f'{prefix}-{len(rows)}-{ts}.csv'
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(flat)
    return path


@dataclass
class OpenAlexScope:
    """Everything a tool needs to search OpenAlex inside a topic, scope and
    quality cut: the extra filter, the lines that report it, and the parts."""
    extra_filter: FilterParts = None
    lines: List[str] = field(default_factory=list)
    info: Dict[str, Any] = field(default_factory=dict)
    topic_scope: Any = None
    cut: Any = None

    @property
    def kwargs(self) -> dict:
        return {'extra_filter': self.extra_filter} if self.extra_filter else {}

    @property
    def summary(self) -> str:
        return '\n'.join(self.lines)


async def resolve_openalex_scope(srv, arguments: dict, query: Optional[str],
                                 issns: Optional[List[str]],
                                 base_extra: Optional[str] = None,
                                 default_types: bool = False) -> OpenAlexScope:
    """Topic scope, journal scope and quality cut as one OpenAlex filter.

    base_extra (e.g. a year range) narrows the venue discovery of the quality
    cut the same way the search will be narrowed. The quality cut needs a
    query and finds the venues first, so it adds requests (see QUALITY_PROPS).
    """
    topic_scope = await build_topic_scope(srv.openalex, arguments, types_alone=default_types)
    scope_part = openalex_issn_filter(issns) if issns else None
    topic_part = topic_scope.filter if topic_scope else None
    out = OpenAlexScope(topic_scope=topic_scope)
    if default_types and not topic_scope:
        # Tools that count papers filter record types even without topics.
        kept_types = parse_work_types(arguments.get("work_types"))
        topic_part = ('type:' + '|'.join(kept_types)) if kept_types else None
        out.lines.append("Record types: " + (', '.join(kept_types) if kept_types else 'all') + ".")
        out.info['work_types'] = kept_types or 'all'
    if topic_scope:
        out.lines.append(topic_scope.report())
        out.info['topic_scope'] = topic_scope.as_dict()
    cut_filter = None
    if arguments.get("min_percentile") is not None:
        if not query:
            raise ValueError("min_percentile needs a query to find the venues of.")
        corpus = and_filters(f'title_and_abstract.search:{_search_value(query)}',
                             base_extra, scope_part, topic_part)
        cut_filter, out.cut = await apply_quality_cut(srv.openalex, srv.client, corpus, arguments)
        out.cut.csv_path = str(_write_rows_csv(out.cut.rows, 'quality-cut'))
        out.lines.append(out.cut.report())
        out.info['quality_cut'] = out.cut.as_dict()
    elif any(arguments.get(k) for k in ('percentile_rule', 'categories', 'include_venues')):
        raise ValueError("percentile_rule, categories and include_venues apply together "
                         "with min_percentile.")
    # The cut's source-ID filter already sits inside scope and topic, so the
    # search needs only the cut plus the topic's type filter.
    out.extra_filter = and_filters(cut_filter, topic_part) if cut_filter else \
        and_filters(scope_part, topic_part)
    return out
