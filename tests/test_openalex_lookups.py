"""Single-record lookups on OpenAlex: get_abstract_details, resolve_identifier,
get_author_profile, and the OpenAlex line of get_quota_status. Fixtures are
hand-built to the documented API shape, not recorded live."""
import asyncio
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from .openalex_mock import client, taxonomy_router

WORK = {
    'id': 'https://openalex.org/W2912800036', 'doi': 'https://doi.org/10.1016/j.jbusres.2019.09.022',
    'title': 'Understanding digital transformation', 'publication_year': 2019,
    'publication_date': '2019-09-01', 'cited_by_count': 4000, 'type': 'article',
    'primary_location': {'source': {'display_name': 'Journal of Business Research'}},
    'authorships': [{'author': {'display_name': 'Gregory Vial'}}],
    'ids': {'openalex': 'https://openalex.org/W2912800036', 'pmid': 'https://pubmed.ncbi.nlm.nih.gov/123',
            'doi': 'https://doi.org/10.1016/j.jbusres.2019.09.022'},
    'abstract_inverted_index': {'Digital': [0], 'change': [1], 'matters': [2]},
}
AUTHOR = {
    'id': 'https://openalex.org/A5023888391', 'display_name': 'Gregory Vial',
    'display_name_alternatives': ['G. Vial'], 'orcid': 'https://orcid.org/0000-0002-1825-0097',
    'works_count': 50, 'cited_by_count': 9000,
    'last_known_institutions': [{'display_name': 'HEC Montreal', 'country_code': 'CA'}],
    'summary_stats': {'h_index': 20, 'i10_index': 25, '2yr_mean_citedness': 3.1},
    'topics': [{'display_name': 'Digital Transformation'}],
    'counts_by_year': [{'year': 2025, 'works_count': 4, 'cited_by_count': 900}],
}


def handler(request, path, p):
    if path.startswith('works/'):
        return httpx.Response(200, json=WORK) if 'W2912800036' in path or '10.1016' in path \
            else httpx.Response(404, json={})
    if path.startswith('authors/'):
        return httpx.Response(200, json=AUTHOR) if 'A5023888391' in path or 'orcid:' in path \
            else httpx.Response(404, json={})


def call(name, args, has_scopus_key, tmp_path, monkeypatch):
    monkeypatch.setenv('SCOPUS_MCP_OUTPUT_DIR', str(tmp_path))
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    scopus = MagicMock()
    scopus.api_key = 'k' if has_scopus_key else None
    with patch.object(server, 'client', scopus), \
            patch.object(server, 'openalex', client(taxonomy_router(handler))):
        text = asyncio.new_event_loop().run_until_complete(
            server.handle_call_tool(name, args))[0].text
    return text, scopus


def test_abstract_details_by_openalex_id_even_with_a_scopus_key(tmp_path, monkeypatch):
    text, scopus = call('get_abstract_details', {'scopus_id': 'W2912800036'}, True, tmp_path, monkeypatch)
    assert 'Digital change matters' in text and "'abstract_source': 'openalex'" in text
    scopus.get_abstract.assert_not_called()


def test_abstract_details_by_doi_without_a_scopus_key(tmp_path, monkeypatch):
    text, _ = call('get_abstract_details', {'scopus_id': '10.1016/j.jbusres.2019.09.022'},
                   False, tmp_path, monkeypatch)
    assert 'Understanding digital transformation' in text and 'Gregory Vial' in text


def test_abstract_details_scopus_id_without_key_is_refused_clearly(tmp_path, monkeypatch):
    text, _ = call('get_abstract_details', {'scopus_id': '0031512927', 'source': 'openalex'},
                   False, tmp_path, monkeypatch)
    assert text.startswith('Error:') and 'OpenAlex' in text


def test_resolve_identifier_through_openalex(tmp_path, monkeypatch):
    text, _ = call('resolve_identifier', {'identifier': '10.1016/j.jbusres.2019.09.022'},
                   False, tmp_path, monkeypatch)
    assert "'openalex_id': 'W2912800036'" in text and "'pubmed_id': '123'" in text
    assert "'scopus_id': None" in text


def test_resolve_identifier_unknown_work(tmp_path, monkeypatch):
    text, _ = call('resolve_identifier', {'identifier': 'W1'}, True, tmp_path, monkeypatch)
    assert 'No OpenAlex record' in text


def test_author_profile_by_openalex_id_and_orcid(tmp_path, monkeypatch):
    for ident in ('A5023888391', '0000-0002-1825-0097'):
        text, scopus = call('get_author_profile', {'author_id': ident}, False, tmp_path, monkeypatch)
        assert "'name': 'Gregory Vial'" in text and "'h_index': 20" in text
        assert "'i10_index': 25" in text and 'HEC Montreal' in text
        scopus.get_author.assert_not_called()


def test_quota_status_reports_openalex_budget(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOPUS_MCP_OUTPUT_DIR', str(tmp_path))
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    scopus = MagicMock()
    scopus.get_quota_status = MagicMock(side_effect=lambda: asyncio.sleep(0, result={}))
    oa = client(taxonomy_router(handler))
    oa.budget = {'remaining_usd': '0.89', 'limit_usd': '1', 'reset_seconds': '100'}
    with patch.object(server, 'client', scopus), patch.object(server, 'openalex', oa):
        text = asyncio.new_event_loop().run_until_complete(
            server.handle_call_tool('get_quota_status', {}))[0].text
    assert 'OpenAlex: $0.89 left of $1 per day' in text
