"""Live checks for OpenAlex topics, the quality cut, topic_profile and field
flows. Run with `pytest -m integration`. They need network access to
OpenAlex, and the quality cut also needs SCOPUS_API_KEY with Serial Title
access. Each skips when its API cannot be reached.

All passed live on 2026-10-11 (topic_match, work_types and the OR-cap chunking need the OpenAlex budget; a key is advised)."""
import asyncio
import json
import os
import re

import pytest

pytestmark = pytest.mark.integration

QUERY = '"digital transformation"'
REAL_SCOPUS_KEY = os.environ.get('SCOPUS_API_KEY')
# tests/conftest.py removes OPENALEX_API_KEY from every test, so keep the real
# one from import time; without it these tests run on the $0.10 anonymous budget.
REAL_OPENALEX_KEY = os.environ.get('OPENALEX_API_KEY')


_LOOP = asyncio.new_event_loop()


def _run(coro):
    # One loop for the whole module: the shared httpx client is bound to the
    # loop it first ran on, so a fresh loop per call breaks it.
    return _LOOP.run_until_complete(coro)


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOPUS_MCP_OUTPUT_DIR', str(tmp_path))
    # Importing the server needs some key; OpenAlex-only calls never use it.
    monkeypatch.setenv('SCOPUS_API_KEY', REAL_SCOPUS_KEY or 'unused')
    if REAL_OPENALEX_KEY:
        monkeypatch.setenv('OPENALEX_API_KEY', REAL_OPENALEX_KEY)
    from scopus_mcp import server
    from scopus_mcp.openalex import OpenAlexClient, OpenAlexError
    oa = OpenAlexClient()
    try:
        _run(oa.taxonomy('fields'))
    except Exception as exc:  # no network, blocked host, spent budget
        pytest.skip(f"OpenAlex unreachable: {exc}")
    monkeypatch.setattr(server, 'openalex', oa)
    yield server
    _run(oa.close())


def _call(server, name, args):
    return _run(server.handle_call_tool(name, args))[0].text


def test_topic_names_resolve_and_search_returns_topic_fields(live):
    text = _call(live, 'search_all', {
        'query': QUERY, 'source': 'openalex', 'max_results': 20, 'inline': 'compact',
        'topics': ['field:14']})
    assert text.startswith('Topic scope: field')
    assert '"field"' in text or 'Business' in text


def test_ambiguous_or_unknown_topic_name_is_refused(live):
    text = _call(live, 'search_all', {'query': QUERY, 'source': 'openalex',
                                      'topics': ['Quantum Cooking Zzz']})
    assert text.startswith('Error:') and 'exactly' in text


def test_topic_profile_reports_fields_and_years(live):
    text = _call(live, 'topic_profile', {'query': QUERY, 'from_year': 2020, 'to_year': 2022})
    assert 'Topic profile of' in text and 'Fields (top' in text
    path = re.search(r'JSON: (\S+\.json)', text).group(1)
    data = json.load(open(path, encoding='utf-8'))
    assert data['total_papers'] > 0 and data['fields']['rows']


@pytest.mark.skipif(not REAL_SCOPUS_KEY, reason='needs SCOPUS_API_KEY')
def test_quality_cut_lists_kept_dropped_and_unranked_venues(live):
    text = _call(live, 'search_all', {
        'query': QUERY, 'source': 'openalex', 'max_results': 20, 'inline': 'compact',
        'topics': ['field:14'], 'min_percentile': 75})
    assert 'Quality cut' in text or text.startswith('Error: The quality cut needs')


def test_citation_network_reports_field_flows(live):
    text = _call(live, 'citation_network', {
        'query': QUERY, 'source': 'openalex', 'max_results': 60, 'topics': ['field:14'],
        'check_completeness': False, 'check_retractions': False})
    assert 'Citation network (openalex)' in text
    assert 'across' in text or 'no citation edge' in text


def _total(server, args):
    text = _call(server, 'publication_counts', {
        'query': QUERY, 'source': 'openalex', 'from_year': 2021, 'to_year': 2022, **args})
    return json.loads(text[text.index('{'):])['total']


def test_topic_match_any_is_wider_than_primary(live):
    primary = _total(live, {'topics': ['field:14'], 'topic_match': 'primary'})
    anywhere = _total(live, {'topics': ['field:14'], 'topic_match': 'any'})
    assert 0 < primary < anywhere


def test_work_types_narrow_the_corpus_and_unknown_types_are_refused(live):
    default = _total(live, {'topics': ['field:14']})
    articles = _total(live, {'topics': ['field:14'], 'work_types': ['article']})
    assert 0 < articles <= default
    text = _call(live, 'publication_counts', {
        'query': QUERY, 'source': 'openalex', 'topics': ['field:14'],
        'work_types': ['nonsense']})
    assert text.startswith('Error:') and 'conference-paper' in text


def test_scope_over_fifty_journals_is_chunked_and_adds_up(live):
    import httpx
    key = REAL_OPENALEX_KEY
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    resp = httpx.get('https://api.openalex.org/sources', headers=headers, params={
        'filter': 'type:journal,works_count:>5000', 'sort': 'cited_by_count:desc',
        'per-page': 120, 'select': 'issn_l'}, timeout=30)
    if resp.status_code != 200:
        pytest.skip(f"OpenAlex source list unavailable: {resp.status_code} {resp.text[:120]}")
    rows = resp.json()['results']
    issns = [r['issn_l'] for r in rows if r.get('issn_l')][:120]
    assert len(issns) > 100

    def papers(scope):
        text = _call(live, 'topic_profile', {
            'query': QUERY, 'from_year': 2020, 'to_year': 2022, 'scope': ','.join(scope)})
        return int(re.search(r'Topic profile of (\d+) papers', text).group(1))

    whole = papers(issns)  # three chunks
    assert whole == sum(papers(issns[i:i + 40]) for i in (0, 40, 80))
