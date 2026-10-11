"""Live checks for OpenAlex topics, the quality cut, topic_profile and field
flows. Run with `pytest -m integration`. They need network access to
OpenAlex, and the quality cut also needs SCOPUS_API_KEY with Serial Title
access. Each skips when its API cannot be reached.

Written without network access to either API, so these have not been run."""
import asyncio
import json
import os
import re

import pytest

pytestmark = pytest.mark.integration

QUERY = '"digital transformation"'
REAL_SCOPUS_KEY = os.environ.get('SCOPUS_API_KEY')


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOPUS_MCP_OUTPUT_DIR', str(tmp_path))
    # Importing the server needs some key; OpenAlex-only calls never use it.
    monkeypatch.setenv('SCOPUS_API_KEY', REAL_SCOPUS_KEY or 'unused')
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
