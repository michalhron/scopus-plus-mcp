"""Field-to-field citations, front field mix and field labels on the main path."""
import asyncio
import json
import os
import re
from unittest.mock import MagicMock, patch

import httpx
import pytest

from scopus_mcp.field_flows import field_mix, flow_matrix, has_levels, path_fields, summary_lines

from .openalex_mock import client, taxonomy_router

CS, BUS = 'Computer Science', 'Business, Management and Accounting'

# W1 (CS) <- W2 (BUS) <- W4 (BUS); W3 (CS) cites W1 and W2; W4 cites W2 and W3.
NODES = {
    'W1': {'year': 2015, 'field': CS, 'subfield': 'Information Systems'},
    'W2': {'year': 2016, 'field': BUS, 'subfield': 'Marketing'},
    'W3': {'year': 2018, 'field': CS, 'subfield': 'Information Systems'},
    'W4': {'year': 2020, 'field': BUS, 'subfield': 'Strategy and Management'},
}
EDGES = [('W2', 'W1'), ('W3', 'W1'), ('W3', 'W2'), ('W4', 'W2'), ('W4', 'W3')]


def test_matrix_counts_and_shares_on_a_hand_built_network():
    f = flow_matrix(NODES, EDGES)
    assert f['matrix_citing_by_cited'] == {BUS: {CS: 2, BUS: 1}, CS: {CS: 1, BUS: 1}}
    assert (f['edges'], f['edges_classified'], f['within'], f['cross']) == (5, 5, 2, 3)
    assert f['within_share'] == 0.4 and f['cross_share'] == 0.6
    assert f['largest_cross_flows'][0] == {'citing': BUS, 'cited': CS, 'n': 2, 'share_of_cross': 0.667}
    assert f['largest_cross_flows'][1]['n'] == 1
    assert f['by_field'][BUS] == {'citations_made': 3, 'within_share': 0.333}


def test_matrix_at_subfield_level():
    f = flow_matrix(NODES, EDGES, level='subfield')
    assert f['matrix_citing_by_cited']['Marketing'] == {'Information Systems': 1}
    assert f['within'] == 1 and f['cross'] == 4


def test_unclassified_ends_are_counted_apart_not_in_shares():
    nodes = {**NODES, 'W5': {'year': 2021}}
    f = flow_matrix(nodes, EDGES + [('W5', 'W1'), ('W4', 'W9')])
    assert f['edges'] == 7 and f['edges_unclassified'] == 2 and f['edges_classified'] == 5
    assert f['within_share'] == 0.4


def test_no_classified_edges():
    f = flow_matrix({'A': {}, 'B': {}}, [('A', 'B')])
    assert f['within_share'] is None and f['largest_cross_flows'] == []
    assert 'no citation edge' in summary_lines(f)[0]
    assert not has_levels({'A': {}})


def test_summary_lines_name_flows():
    text = ' '.join(summary_lines(flow_matrix(NODES, EDGES)))
    assert '40% within' in text and '60% across' in text
    assert f'{BUS} → {CS} 2' in text


def test_field_mix_classification():
    nodes = {k: {'field': CS} for k in 'abcd'}
    nodes['e'] = {'field': BUS}
    assert field_mix('abcd', nodes)['kind'] == 'single field'
    assert field_mix('abcde', nodes)['kind'] == 'single field'      # 4 of 5 = 80%
    nodes['f'] = {'field': BUS}
    assert field_mix('abcdef', nodes)['kind'] == 'mixed'            # 4 of 6
    assert field_mix(['a', 'b', 'e', 'f'], nodes)['kind'] == 'meeting point'
    assert field_mix(['x'], {'x': {}}) is None


def test_path_fields_marks_crossings():
    p = path_fields(['W1', 'W2', 'W3', 'W4'], NODES)
    assert p['route'] == [CS, BUS, CS, BUS] and len(p['crossings']) == 3
    assert p['crossings'][0] == {'from': 'W1', 'to': 'W2', 'from_field': CS, 'to_field': BUS}
    assert path_fields(['W1', 'W3'], NODES)['crossings'] == []


# ---------------------------------------------------------------------------
# Through the tools, on OpenAlex mocks
# ---------------------------------------------------------------------------

def _work(wid, year, field, refs, subfield='X'):
    topic = {'id': 'https://openalex.org/T1', 'display_name': 'T',
             'subfield': {'id': 'https://openalex.org/subfields/1', 'display_name': subfield},
             'field': {'id': 'https://openalex.org/fields/1', 'display_name': field},
             'domain': {'id': 'https://openalex.org/domains/1', 'display_name': 'D'}} if field else None
    return {'id': f'https://openalex.org/{wid}', 'title': f'Paper {wid}', 'publication_year': year,
            'publication_date': f'{year}-01-01', 'cited_by_count': 10 - int(wid[1:]), 'type': 'article',
            'primary_location': {'source': {'display_name': 'J'}}, 'authorships': [],
            'referenced_works': [f'https://openalex.org/{r}' for r in refs],
            'primary_topic': topic}


WORKS = [_work('W1', 2015, CS, []), _work('W2', 2016, BUS, ['W1']),
         _work('W3', 2018, CS, ['W1', 'W2']), _work('W4', 2020, BUS, ['W2', 'W3'])]


def _call(name, args, works, tmp_path, monkeypatch):
    monkeypatch.setenv('SCOPUS_MCP_OUTPUT_DIR', str(tmp_path))

    def extra(request, path, p):
        if path == 'works':
            return httpx.Response(200, json={'meta': {'count': len(works), 'next_cursor': None},
                                             'results': works})
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'client', MagicMock()), \
            patch.object(server, 'openalex', client(taxonomy_router(extra))):
        return asyncio.new_event_loop().run_until_complete(
            server.handle_call_tool(name, args))[0].text


def _json_from(text):
    return json.loads(open(re.search(r'JSON: (\S+\.json)', text).group(1), encoding='utf-8').read())


def test_citation_network_reports_field_flows_and_main_path_fields(tmp_path, monkeypatch):
    text = _call('citation_network', {'query': 'x', 'source': 'openalex',
                                      'check_completeness': False, 'check_retractions': False},
                 WORKS, tmp_path, monkeypatch)
    assert '40% within a field, 60% across' in text
    assert f'{BUS} → {CS}' in text and 'Main path fields:' in text
    data = _json_from(text)
    assert data['field_flows']['within'] == 2 and data['field_flows']['cross'] == 3
    assert data['main_path_fields']['papers'][0]['field'] in (CS, BUS)
    assert data['nodes'][0]['field'] and data['nodes'][0]['subfield']


def test_citation_network_flow_level_subfield(tmp_path, monkeypatch):
    text = _call('citation_network', {'query': 'x', 'source': 'openalex', 'flow_level': 'subfield',
                                      'check_completeness': False, 'check_retractions': False},
                 WORKS, tmp_path, monkeypatch)
    assert 'by subfield' in text


def test_citation_network_without_topics_in_records_adds_no_field_output(tmp_path, monkeypatch):
    bare = [_work(w['id'][-2:], int(w['publication_year']), None, []) for w in WORKS]
    for b, w in zip(bare, WORKS):
        b['referenced_works'] = w['referenced_works']
    text = _call('citation_network', {'query': 'x', 'source': 'openalex',
                                      'check_completeness': False, 'check_retractions': False},
                 bare, tmp_path, monkeypatch)
    assert 'across' not in text and 'field_flows' not in _json_from(text)
    assert 'field' not in _json_from(text)['nodes'][0]


def test_describe_fronts_adds_field_mix_only_when_nodes_have_fields():
    from scopus_mcp.fronts import cluster_citation_network, describe_fronts
    nodes = {k: {**v, 'id': k, 'title': k, 'creator': 'A', 'year': str(v['year']), 'parents': []}
             for k, v in NODES.items()}
    for citing, cited in EDGES:
        nodes[citing]['parents'].append(cited)
    fronts = describe_fronts(nodes, cluster_citation_network(nodes, min_size=3), {})
    mix = fronts[0]['field_mix']
    assert mix['dominant_share'] == 0.5 and mix['kind'] == 'meeting point'
    assert {f['field'] for f in mix['fields']} == {CS, BUS}
    plain = {k: {kk: vv for kk, vv in v.items() if kk not in ('field', 'subfield')}
             for k, v in nodes.items()}
    assert 'field_mix' not in describe_fronts(plain, cluster_citation_network(plain, min_size=3), {})[0]


def test_research_fronts_tool_shows_field_mix_and_main_path_fields(tmp_path, monkeypatch):
    text = _call('research_fronts', {'query': 'x', 'source': 'openalex', 'min_size': 3},
                 WORKS, tmp_path, monkeypatch)
    assert 'fields: ' in text and '(meeting point)' in text
    assert 'Main path across fields:' in text
    data = _json_from(text)
    assert data['fronts'][0]['field_mix']['kind'] == 'meeting point'
    assert 'main_path_fields' in data
