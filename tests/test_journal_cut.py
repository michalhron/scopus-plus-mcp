"""Field-relative quality cut: venues, CiteScore percentiles, reporting."""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scopus_mcp.baskets import issns_from_file, resolve_scope
from scopus_mcp.journal_cut import apply_quality_cut, parse_include

from .openalex_mock import client, taxonomy_router


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# (id, title, issns, oa type, works, scopus ranks [(asjc, percentile)], scopus type)
VENUES = [
    ('S1', 'MIS Quarterly', ['0276-7783', '2162-9730'], 'journal', 50,
     [('1710', 98), ('1803', 90)], 'Journal'),
    ('S2', 'Journal of Marketing', ['0022-2429'], 'journal', 30, [('1406', 80)], 'Journal'),
    ('S3', 'Middling Journal', ['1111-1111'], 'journal', 20,
     [('1710', 40), ('1803', 80)], 'Journal'),
    ('S4', 'ICIS Proceedings', [], 'conference', 15, None, None),
    ('S5', 'Obscure Review', ['2222-2222'], 'journal', 10, None, None),  # not in Scopus
    ('S6', 'Procedia Something', ['3333-3333'], 'journal', 5, [('1710', 99)],
     'Conference Proceeding'),
    ('S7', 'New Journal', ['4444-4444'], 'journal', 4, [], 'Journal'),  # no CiteScore year
]
UNKNOWN_WORKS = 7
TOTAL = sum(v[4] for v in VENUES) + UNKNOWN_WORKS


def _entry(issns, ranks, agg, title):
    e = {'dc:title': title, 'source-id': '1', 'prism:issn': issns[0].replace('-', ''),
         'prism:eIssn': issns[1].replace('-', '') if len(issns) > 1 else None,
         'prism:aggregationType': agg, 'subject-area': [
             {'@code': c, '$': {'1710': 'Information Systems', '1803': 'Management Science',
                                '1406': 'Marketing'}[c]} for c, _ in ranks]}
    if ranks:
        e['citeScoreYearInfoList'] = {'citeScoreYearInfo': [{
            '@year': '2024', '@status': 'Complete', 'citeScoreInformationList': [{
                'citeScoreInfo': [{'docType': 'all', 'citeScore': '9.9', 'citeScoreSubjectRank': [
                    {'subjectCode': c, 'rank': '1', 'percentile': str(p)} for c, p in ranks]}]}]}]}
    return e


def scopus(fail=False):
    s = MagicMock()
    entries = [_entry(v[2], v[5], v[6], v[1]) for v in VENUES if v[5] is not None and v[2]]
    if fail:
        s.serial_titles = AsyncMock(side_effect=RuntimeError('AUTHORIZATION_ERROR'))
    else:
        s.serial_titles = AsyncMock(return_value=entries)
    s.asjc_categories = AsyncMock(return_value=[
        {'code': '1710', 'detail': 'Information Systems', 'description': 'Computer Science'},
        {'code': '1803', 'detail': 'Management Science and Operations Research',
         'description': 'Decision Sciences'},
        {'code': '1406', 'detail': 'Marketing', 'description': 'Business'}])
    return s


def oa(venues=VENUES, extra=None):
    def handler(request, path, p):
        if extra:
            r = extra(request, path, p)
            if r is not None:
                return r
        if path == 'works' and p.get('group_by') == 'primary_location.source.id':
            groups = [{'key': f'https://openalex.org/{v[0]}', 'key_display_name': v[1],
                       'count': v[4]} for v in venues]
            groups.append({'key': 'unknown', 'key_display_name': 'unknown',
                           'count': UNKNOWN_WORKS})
            return httpx.Response(200, json={'meta': {'count': TOTAL}, 'group_by': groups})
        if path == 'sources':
            wanted = p['filter'].split(':', 1)[1].split('|')
            res = [{'id': f'https://openalex.org/{v[0]}', 'display_name': v[1],
                    'issn_l': v[2][0] if v[2] else None, 'issn': v[2] or None, 'type': v[3]}
                   for v in venues if v[0] in wanted]
            return httpx.Response(200, json={'results': res})
    return client(taxonomy_router(handler))


def cut(args, venues=VENUES, scopus_client=None):
    return _run(apply_quality_cut(oa(venues), scopus_client or scopus(),
                                  'title_and_abstract.search:x', args))


def titles(rows):
    return [r['title'] for r in rows]


def test_best_rule_keeps_journal_with_one_strong_category():
    flt, rep = cut({'min_percentile': 75, 'percentile_rule': 'best'})
    assert titles(rep.kept) == ['MIS Quarterly', 'Journal of Marketing', 'Middling Journal']
    assert flt == 'primary_location.source.id:S1|S2|S3'
    mis = rep.kept[0]
    assert (mis['percentile'], mis['category']) == (98, 'Information Systems')


def test_all_rule_requires_every_category():
    flt, rep = cut({'min_percentile': 75, 'percentile_rule': 'all'})
    assert titles(rep.kept) == ['MIS Quarterly', 'Journal of Marketing']
    assert titles(rep.dropped) == ['Middling Journal']
    assert rep.dropped[0]['percentile'] == 40 and rep.dropped[0]['category'] == 'Information Systems'
    assert flt == 'primary_location.source.id:S1|S2'


def test_default_rule_is_best():
    _, rep = cut({'min_percentile': 75})
    assert rep.percentile_rule == 'best' and len(rep.kept) == 3


def test_venues_without_citescore_are_listed_not_dropped_silently():
    _, rep = cut({'min_percentile': 75})
    why = {r['title']: r['why'] for r in rep.no_citescore}
    assert set(why) == {'ICIS Proceedings', 'Obscure Review', 'Procedia Something', 'New Journal'}
    assert why['Obscure Review'] == 'not found in Scopus'
    assert 'conference proceedings' in why['Procedia Something']
    assert why['New Journal'] == 'no complete CiteScore year'
    # Proceedings and book series are called out separately.
    assert rep.proceedings_dropped == {'venues': 2, 'works': 20}
    assert 'Proceedings or book series dropped: 2 venues, 20 papers' in rep.report()


def test_share_removed_arithmetic():
    _, rep = cut({'min_percentile': 75, 'percentile_rule': 'all'})
    assert rep.works_in_corpus == TOTAL
    assert rep.works_kept == 80          # 50 + 30
    assert rep.share_removed == round(1 - 80 / TOTAL, 3)
    assert rep.works_without_venue == UNKNOWN_WORKS
    assert f'80 of {TOTAL} papers kept' in rep.report()


def test_include_venues_bypass_by_source_id_and_issn():
    flt, rep = cut({'min_percentile': 75, 'percentile_rule': 'all',
                    'include_venues': ['S4', '2222-2222']})
    assert titles(rep.included) == ['ICIS Proceedings', 'Obscure Review']
    assert set(flt.split(':')[1].split('|')) == {'S1', 'S2', 'S4', 'S5'}
    assert 'include_venues' in rep.included[0]['reason']
    assert rep.works_kept == 80 + 15 + 10
    assert rep.proceedings_dropped['venues'] == 1  # Procedia only


def test_include_venues_rejects_garbage():
    with pytest.raises(ValueError, match='neither an ISSN'):
        parse_include(['MISQ'])
    ids, issns = parse_include(['https://openalex.org/S9', '0276-7783'])
    assert ids == {'S9'} and issns == {'02767783'}


def test_min_percentile_zero_means_scopus_indexed_journals_only():
    flt, rep = cut({'min_percentile': 0})
    assert titles(rep.kept) == ['MIS Quarterly', 'Journal of Marketing', 'Middling Journal',
                                'New Journal']
    assert 'indexed in Scopus' in rep.report()
    assert not rep.dropped


def test_categories_restrict_which_ranks_count():
    flt, rep = cut({'min_percentile': 75, 'percentile_rule': 'best',
                    'categories': ['Information Systems']})
    # Middling is 40th in IS; its 80th in management science no longer counts.
    assert titles(rep.kept) == ['MIS Quarterly']
    by = {r['title']: r for r in rep.dropped}
    assert by['Journal of Marketing']['why'] == 'ranked in none of the chosen categories'
    assert by['Middling Journal']['percentile'] == 40
    assert rep.categories == ['Information Systems']


def test_scopus_failure_refuses_with_clear_message_and_no_fallback():
    with pytest.raises(ValueError) as exc:
        cut({'min_percentile': 75}, scopus_client=scopus(fail=True))
    msg = str(exc.value)
    assert 'Serial Title' in msg and 'no fallback' in msg and 'AUTHORIZATION_ERROR' in msg


def test_nothing_kept_raises_instead_of_widening_to_the_whole_corpus():
    with pytest.raises(ValueError, match='No venue clears'):
        cut({'min_percentile': 99, 'percentile_rule': 'all'}, venues=[VENUES[2]])


def test_empty_corpus_raises():
    with pytest.raises(ValueError, match='nothing to cut'):
        cut({'min_percentile': 75}, venues=[])


def test_bad_arguments():
    with pytest.raises(ValueError, match='percentile_rule'):
        cut({'min_percentile': 75, 'percentile_rule': 'median'})
    with pytest.raises(ValueError, match='between 0 and 99'):
        cut({'min_percentile': 100})


def test_kept_filter_chunks_over_fifty_sources_and_scopus_batches_issns():
    many = [(f'S{1000 + i}', f'J{i}', [f'{i:04d}-{i:04d}'], 'journal', 1, [('1710', 90)], 'Journal')
            for i in range(120)]
    s = scopus()
    s.serial_titles = AsyncMock(return_value=[_entry(v[2], v[5], v[6], v[1]) for v in many])
    flt, rep = cut({'min_percentile': 75}, venues=many, scopus_client=s)
    assert isinstance(flt, list) and len(flt) == 3
    assert rep.requests['openalex_source_lookups'] == 3
    assert rep.requests['scopus_serial_title'] == 5   # 120 ISSNs / 25 per request


def test_scopus_lookup_tries_every_openalex_issn():
    # Serial Title knows MISQ only under its electronic ISSN.
    s = scopus()
    s.serial_titles = AsyncMock(return_value=[_entry(['2162-9730'], [('1710', 98)], 'Journal', 'MISQ')])
    venues = [VENUES[0]]
    _, rep = cut({'min_percentile': 75}, venues=venues, scopus_client=s)
    assert titles(rep.kept) == ['MIS Quarterly']
    sent = s.serial_titles.await_args.args[0]
    assert sent == ['02767783', '21629730']


# ---------------------------------------------------------------------------
# Through the tools
# ---------------------------------------------------------------------------

def _call(name, args, openalex, scopus_client):
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'client', scopus_client), patch.object(server, 'openalex', openalex):
        return asyncio.new_event_loop().run_until_complete(
            server.handle_call_tool(name, args))[0].text


def test_search_all_with_topics_and_cut_ands_them_and_reports(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOPUS_MCP_OUTPUT_DIR', str(tmp_path))
    seen = []

    def extra(request, path, p):
        if path == 'works' and 'group_by' in p:
            seen.append(('group', p['filter']))
        elif path == 'works':
            seen.append(('works', p['filter']))
            return httpx.Response(200, json={'meta': {'count': 0, 'next_cursor': None},
                                             'results': []})
    text = _call('search_all', {'query': 'digital transformation', 'source': 'openalex',
                                'topics': ['T10346'], 'min_percentile': 75,
                                'percentile_rule': 'all'}, oa(extra=extra), scopus())
    group = next(f for k, f in seen if k == 'group')
    final = next(f for k, f in seen if k == 'works')
    # Venue discovery sees the whole corpus filter, the search sees the kept sources.
    assert 'primary_topic.id:T10346' in group and 'type:article' in group
    assert 'primary_location.source.id:S1|S2' in final and 'primary_topic.id:T10346' in final
    assert text.startswith('Topic scope:') and 'Quality cut' in text
    assert '2 journals kept, 1 below the cut, 4 venues with no CiteScore' in text
    assert list(tmp_path.glob('quality-cut-*.csv'))


def test_cut_without_query_matching_scopus_source_is_refused():
    text = _call('search_all', {'query': 'x', 'min_percentile': 75}, MagicMock(), MagicMock())
    assert text.startswith('Error:') and "source='openalex'" in text


def test_cut_options_without_min_percentile_are_refused():
    text = _call('search_all', {'query': 'x', 'source': 'openalex', 'include_venues': ['S1']},
                 oa(), scopus())
    assert text.startswith('Error:') and 'min_percentile' in text


# ---------------------------------------------------------------------------
# CSV scope
# ---------------------------------------------------------------------------

def test_scope_from_find_journals_style_csv(tmp_path):
    f = tmp_path / 'journals.csv'
    f.write_text('title,issn,eissn,percentile\nMISQ,0276-7783,2162-9730,98\nISR,1047-7047,,95\n'
                 'Odd,n/a,,10\n', encoding='utf-8')
    assert resolve_scope(str(f)) == ['0276-7783', '2162-9730', '1047-7047']


def test_scope_csv_semicolons_and_multi_issn_cells(tmp_path):
    f = tmp_path / 'ajg.csv'
    f.write_text('Journal;ISSN\nA;1350-1917, 1365-2575\nB;0742-1222\n', encoding='utf-8-sig')
    assert issns_from_file(str(f)) == ['1350-1917', '1365-2575', '0742-1222']


def test_scope_csv_errors(tmp_path):
    with pytest.raises(ValueError, match='does not exist'):
        resolve_scope(str(tmp_path / 'missing.csv'))
    bad = tmp_path / 'b.csv'
    bad.write_text('name,rank\nA,1\n', encoding='utf-8')
    with pytest.raises(ValueError, match="no column with 'issn'"):
        resolve_scope(str(bad))
    empty = tmp_path / 'e.csv'
    empty.write_text('issn\nn/a\n', encoding='utf-8')
    with pytest.raises(ValueError, match='no valid ISSNs'):
        resolve_scope(str(empty))
    assert resolve_scope('0276-7783') == ['0276-7783']  # ISSNs and baskets still work


def test_group_by_never_asks_for_page_two():
    """OpenAlex rejects group_by beyond page 1 (live 400, 2026-10-11)."""
    seen = []

    def handler(request, path, p):
        if path == 'works' and 'group_by' in p:
            seen.append(dict(p))
            if 'page' in p:
                return httpx.Response(400, json={'error': 'Pagination error.'})
            groups = [{'key': f'https://openalex.org/S{i}', 'key_display_name': f'J{i}',
                       'count': 1} for i in range(200)]
            return httpx.Response(200, json={'meta': {'count': 5000}, 'group_by': groups})

    groups, total = _run(client(taxonomy_router(handler)).group_by(
        'title_and_abstract.search:x', 'primary_location.source.id', max_groups=2000))
    assert len(groups) == 200 and total == 5000 and len(seen) == 1
