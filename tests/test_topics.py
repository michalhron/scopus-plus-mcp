"""Topic scope on OpenAlex: name resolution, filters, wiring into the tools."""
import asyncio
import json
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from scopus_mcp import openalex as oa
from scopus_mcp.baskets import openalex_issn_filter
from scopus_mcp.openalex import and_filters, chunk_or, clean_openalex_work
from scopus_mcp.topics import (
    DEFAULT_WORK_TYPES, build_topic_scope, parse_work_types, reject_topics_for_scopus,
    resolve_topics, topic_filter,
)

from .openalex_mock import client, fixture, taxonomy_router


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------

def test_exact_name_resolves_to_a_topic():
    c = client(taxonomy_router())
    out = _run(resolve_topics(c, ['Digital Transformation']))
    assert out == [{'id': 'T12345', 'name': 'Digital Transformation', 'level': 'topic'}]


def test_exact_name_is_case_and_punctuation_insensitive():
    c = client(taxonomy_router())
    out = _run(resolve_topics(c, ['business, management and accounting']))
    assert out[0] == {'id': '14', 'name': 'Business, Management and Accounting', 'level': 'field'}


def test_ambiguous_name_lists_candidates():
    # 'Marketing' is both a subfield and a topic.
    c = client(taxonomy_router())
    with pytest.raises(ValueError) as exc:
        _run(resolve_topics(c, ['Marketing']))
    msg = str(exc.value)
    assert 'several entities' in msg and 'subfield:1406' in msg and 'T11111' in msg


def test_unknown_name_is_refused_with_close_matches_never_fuzzy_matched():
    c = client(taxonomy_router())
    with pytest.raises(ValueError) as exc:
        _run(resolve_topics(c, ['Digital']))
    assert 'exactly' in str(exc.value) and 'Digital Transformation in Business' in str(exc.value)


def test_name_with_no_match_at_all():
    c = client(taxonomy_router())
    with pytest.raises(ValueError, match='No topic, subfield or field is named exactly'):
        _run(resolve_topics(c, ['Quantum Cooking']))


def test_id_forms():
    c = client(taxonomy_router())
    out = _run(resolve_topics(c, ['subfield:1710', 'subfields/1405']))
    assert [(e['id'], e['level']) for e in out] == [('1710', 'subfield'), ('1405', 'subfield')]
    topic = _run(resolve_topics(c, ['https://openalex.org/T10346']))
    assert topic[0]['name'] == 'Digital Transformation in Business'


def test_unknown_ids_are_refused():
    c = client(taxonomy_router())
    with pytest.raises(ValueError, match='No OpenAlex topic T1'):
        _run(resolve_topics(c, ['T1']))
    with pytest.raises(ValueError, match='No OpenAlex field 99'):
        _run(resolve_topics(c, ['field:99']))


def test_mixed_levels_are_refused():
    c = client(taxonomy_router())
    with pytest.raises(ValueError, match='mixes levels'):
        _run(resolve_topics(c, ['T10346', 'field:17']))


def test_taxonomy_is_fetched_once_per_name_batch_not_per_name():
    handler = taxonomy_router()
    c = client(handler)
    _run(resolve_topics(c, ['Digital Transformation', 'Digital Platforms and Ecosystems']))
    paths = [p for p, _ in handler.seen]
    assert paths.count('topics') == 2 and paths.count('fields') == 1  # fields once per call
    handler.seen.clear()
    _run(resolve_topics(c, ['subfield:1710', 'subfield:1405']))
    assert [p for p, _ in handler.seen] == ['subfields', 'subfields']


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

ENT = [{'id': 'T1', 'name': 'A', 'level': 'topic'}, {'id': 'T2', 'name': 'B', 'level': 'topic'}]


def test_several_topics_combine_as_or():
    assert topic_filter(ENT, 'primary') == 'primary_topic.id:T1|T2'


def test_primary_vs_any_filter_strings():
    sub = [{'id': '1710', 'name': 'IS', 'level': 'subfield'}]
    fld = [{'id': '17', 'name': 'CS', 'level': 'field'}]
    assert topic_filter(sub, 'primary') == 'primary_topic.subfield.id:1710'
    assert topic_filter(sub, 'any') == 'topics.subfield.id:1710'
    assert topic_filter(fld, 'primary') == 'primary_topic.field.id:17'
    assert topic_filter(fld, 'any') == 'topics.field.id:17'
    assert topic_filter(ENT, 'any') == 'topics.id:T1|T2'


def test_work_types_default_list_and_all():
    assert parse_work_types(None) == list(DEFAULT_WORK_TYPES)
    assert parse_work_types('all') is None
    assert parse_work_types(['Article', 'dataset']) == ['article', 'dataset']
    assert parse_work_types('article,review') == ['article', 'review']


def test_build_scope_filter_includes_type_default_and_all():
    c = client(taxonomy_router())
    s = _run(build_topic_scope(c, {'topics': ['T10346']}))
    assert s.filter == 'primary_topic.id:T10346,type:article|review|book|book-chapter'
    s = _run(build_topic_scope(c, {'topics': ['T10346'], 'work_types': 'all',
                                   'topic_match': 'any'}))
    assert s.filter == 'topics.id:T10346'
    assert 'all types' in s.report() and 'any assigned topic' in s.report()


def test_report_names_topics_ids_level_match_and_types():
    c = client(taxonomy_router())
    s = _run(build_topic_scope(c, {'topics': ['subfield:1710', 'subfield:1405']}))
    text = s.report()
    assert 'subfields' in text and 'Information Systems (1710)' in text
    assert 'primary topic' in text and 'article, review, book, book-chapter' in text


def test_no_topics_means_no_scope_and_work_types_alone_is_an_error():
    c = client(taxonomy_router())
    assert _run(build_topic_scope(c, {})) is None
    with pytest.raises(ValueError, match='together with topics'):
        _run(build_topic_scope(c, {'work_types': 'article'}))


def test_bad_topic_match():
    c = client(taxonomy_router())
    with pytest.raises(ValueError, match='topic_match'):
        _run(build_topic_scope(c, {'topics': ['T10346'], 'topic_match': 'some'}))


def test_scopus_source_rejects_topics_naming_openalex():
    with pytest.raises(ValueError, match="source='openalex'"):
        reject_topics_for_scopus({'topics': ['T1']})
    reject_topics_for_scopus({'query': 'x'})  # no topics: fine


# ---------------------------------------------------------------------------
# Filter chunking and AND with scope
# ---------------------------------------------------------------------------

def test_chunk_or_splits_long_lists_and_keeps_short_ones_a_string():
    assert chunk_or('k', ['a', 'b']) == 'k:a|b'
    parts = chunk_or('k', [str(i) for i in range(120)])
    assert len(parts) == 3 and parts[0].count('|') == 49 and parts[2].count('|') == 19
    assert chunk_or('k', []) is None


def test_issn_filter_chunks_over_fifty():
    issns = [f'{i:04d}-0000' for i in range(60)]
    f = openalex_issn_filter(issns)
    assert isinstance(f, list) and len(f) == 2
    assert openalex_issn_filter(issns[:3]).startswith('primary_location.source.issn:0000-0000|')


def test_and_filters_cross_product_and_none():
    assert and_filters(None, None) is None
    assert and_filters('a:1', 'b:2') == 'a:1,b:2'
    assert and_filters(['a:1', 'a:2'], 'b:3') == ['a:1,b:3', 'a:2,b:3']
    assert and_filters(['a:1', 'a:2'], ['b:3', 'b:4']) == [
        'a:1,b:3', 'a:1,b:4', 'a:2,b:3', 'a:2,b:4']


def _work(wid, cites=1, date='2020-01-01'):
    return {'id': f'https://openalex.org/{wid}', 'title': wid, 'publication_year': 2020,
            'publication_date': date, 'cited_by_count': cites, 'type': 'article'}


def test_chunked_search_merges_and_deduplicates_across_chunks():
    calls = []

    def extra(request, path, p):
        if path != 'works':
            return None
        calls.append(p['filter'])
        works = [_work('W1', 5), _work('W2', 3)] if 'issn:A' in p['filter'] else \
            [_work('W2', 3), _work('W3', 9)]
        return httpx.Response(200, json={'meta': {'count': len(works), 'next_cursor': None},
                                         'results': works})
    c = client(taxonomy_router(extra))
    works, meta = _run(c.search('x', max_results=10, sort='citedby',
                                extra_filter=['primary_location.source.issn:A',
                                              'primary_location.source.issn:B']))
    assert [w['id'].rsplit('/', 1)[-1] for w in works] == ['W3', 'W1', 'W2']
    assert len(calls) == 2 and all('title_and_abstract.search:x' in f for f in calls)
    assert meta['chunks'] == 2 and meta['total_fetched'] == 3


def test_chunked_search_cuts_to_max_results_and_flags_truncation():
    def extra(request, path, p):
        if path != 'works':
            return None
        works = [_work('W1', 1), _work('W2', 8)] if 'issn:A' in p['filter'] else [_work('W3', 5)]
        return httpx.Response(200, json={'meta': {'count': len(works), 'next_cursor': None},
                                         'results': works})
    c = client(taxonomy_router(extra))
    works, meta = _run(c.search('x', max_results=2, sort='citedby',
                                extra_filter=['f:issn:A', 'f:issn:B']))
    assert [w['id'][-2:] for w in works] == ['W2', 'W3'] and meta['truncated'] is True


def test_yearly_counts_with_topic_filter_and_chunks_sum():
    seen = []

    def extra(request, path, p):
        if path == 'works' and p.get('group_by') == 'publication_year':
            seen.append(p['filter'])
            return httpx.Response(200, json={'meta': {'count': 3}, 'group_by': [
                {'key': '2020', 'count': 2}, {'key': '2021', 'count': 1}]})
    c = client(taxonomy_router(extra))
    counts, total = _run(c.yearly_counts('x', 2019, 2021, extra_filter=['a:1', 'a:2']))
    assert counts == {2020: 4, 2021: 2} and total == 6 and len(seen) == 2
    assert seen[0].endswith('publication_year:2019-2021,a:1')


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _call(name, args, openalex, scopus=None):
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'client', scopus or MagicMock()), \
         patch.object(server, 'openalex', openalex):
        return _run(server.handle_call_tool(name, args))[0].text


def test_search_all_openalex_applies_topics_scope_and_type_filter_and_reports_it():
    filters = []

    def extra(request, path, p):
        if path == 'works':
            filters.append(p['filter'])
            return httpx.Response(200, json={'meta': {'count': 1, 'next_cursor': None},
                                             'results': [fixture('sample_work.json')]})
    c = client(taxonomy_router(extra))
    text = _call('search_all', {'query': 'digital transformation', 'source': 'openalex',
                                'topics': ['T10346'], 'scope': 'basket_of_eight'}, c)
    assert text.startswith('Topic scope: topic Digital Transformation in Business (T10346)')
    f = filters[0]
    assert 'primary_topic.id:T10346' in f and 'type:article|review|book|book-chapter' in f
    assert 'primary_location.source.issn:' in f  # topics AND scope
    assert "'topic': 'Digital Transformation in Business'" in text
    assert "'field': 'Computer Science'" in text


def test_search_all_work_types_all_drops_the_type_filter():
    filters = []

    def extra(request, path, p):
        if path == 'works':
            filters.append(p['filter'])
    c = client(taxonomy_router(extra))
    _call('search_all', {'query': 'x', 'source': 'openalex', 'topics': ['T10346'],
                         'work_types': 'all'}, c)
    assert 'type:' not in filters[0].replace('title_and_abstract', '')


def test_search_all_without_topics_sends_no_topic_or_type_filter():
    filters = []

    def extra(request, path, p):
        if path == 'works':
            filters.append(p['filter'])
    c = client(taxonomy_router(extra))
    _call('search_all', {'query': 'x', 'source': 'openalex'}, c)
    assert filters == ['title_and_abstract.search:x']


def test_search_all_scopus_with_topics_raises_naming_openalex():
    text = _call('search_all', {'query': 'x', 'topics': ['T10346']}, MagicMock())
    assert text.startswith('Error:') and "source='openalex'" in text


def test_publication_counts_with_topics_reports_scope():
    seen = []

    def extra(request, path, p):
        if path == 'works':
            seen.append(p['filter'])
            return httpx.Response(200, json={'meta': {'count': 2}, 'group_by': [
                {'key': '2020', 'count': 2}]})
    c = client(taxonomy_router(extra))
    out = json.loads(_call('publication_counts', {
        'query': 'x', 'source': 'openalex', 'from_year': 2020, 'to_year': 2020,
        'topics': ['field:17'], 'topic_match': 'any'}, c))
    assert out['topic_scope']['level'] == 'field' and out['topic_scope']['match'] == 'any'
    assert 'topics.field.id:17' in seen[0]
    assert out['scope_summary'].startswith('Topic scope: field')


def test_publication_counts_scopus_with_topics_raises():
    text = _call('publication_counts', {'query': 'x', 'from_year': 2020, 'to_year': 2021,
                                        'topics': ['T1']}, MagicMock())
    assert text.startswith('Error:') and "source='openalex'" in text


def test_records_without_a_topic_do_not_crash_the_tools():
    bare = {**fixture('sample_work.json'), 'primary_topic': None}

    def extra(request, path, p):
        if path == 'works':
            return httpx.Response(200, json={'meta': {'count': 1, 'next_cursor': None},
                                             'results': [bare]})
    c = client(taxonomy_router(extra))
    text = _call('search_all', {'query': 'x', 'source': 'openalex', 'inline': 'compact'}, c)
    assert 'Digital transformation' in text
    rec = clean_openalex_work(bare)
    assert rec['topic'] is None and rec['field'] is None
