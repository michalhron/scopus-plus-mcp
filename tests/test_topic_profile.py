"""topic_profile: grouped requests, shares, per-year fields, first year over."""
import asyncio
import json
import os
import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scopus_mcp.topic_profile import field_by_year, first_year_over, ranked

from .openalex_mock import client, taxonomy_router

FIELDS = {'17': 'Computer Science', '14': 'Business, Management and Accounting',
          '33': 'Social Sciences'}

# Per year: {field id: papers}; the year total includes 'unknown'.
YEARS = {
    2019: ({'17': 8, '14': 1}, 10),     # unknown 1
    2020: ({'17': 8, '14': 5, '33': 1}, 15),   # unknown 1
    2021: ({'17': 10, '14': 8, '33': 2}, 21),  # unknown 1
}
TOTAL = sum(t for _, t in YEARS.values())


def _group(key, name, count):
    return {'key': f'https://openalex.org/fields/{key}' if key.isdigit() else key,
            'key_display_name': name, 'count': count}


def extra(request, path, p):
    if path != 'works' or 'group_by' not in p:
        return None
    flt = p['filter']
    m = re.search(r'publication_year:(\d{4})(?![-\d])', flt)
    year = int(m.group(1)) if m else None
    assert flt.count('publication_year') <= 1  # a filter key is never repeated
    key = p['group_by']
    if key == 'publication_year':
        return httpx.Response(200, json={'meta': {'count': TOTAL}, 'group_by': [
            {'key': str(y), 'key_display_name': str(y), 'count': t}
            for y, (_, t) in YEARS.items()]})
    if key == 'primary_topic.field.id':
        if year:
            counts, total = YEARS[year]
            groups = [_group(k, FIELDS[k], n) for k, n in counts.items()]
            groups.append(_group('unknown', 'unknown', total - sum(counts.values())))
            return httpx.Response(200, json={'meta': {'count': total}, 'group_by': groups})
        agg = {}
        for counts, _ in YEARS.values():
            for k, n in counts.items():
                agg[k] = agg.get(k, 0) + n
        groups = [_group(k, FIELDS[k], n) for k, n in agg.items()]
        groups.append(_group('unknown', 'unknown', 3))
        return httpx.Response(200, json={'meta': {'count': TOTAL}, 'group_by': groups})
    if key == 'primary_topic.subfield.id':
        return httpx.Response(200, json={'meta': {'count': TOTAL}, 'group_by': [
            {'key': 'https://openalex.org/subfields/1710', 'key_display_name': 'Information Systems',
             'count': 30}, {'key': 'https://openalex.org/subfields/1405',
                            'key_display_name': 'Management Information Systems', 'count': 10}]})
    if key == 'primary_topic.id':
        return httpx.Response(200, json={'meta': {'count': TOTAL}, 'group_by': [
            {'key': 'https://openalex.org/T10346', 'key_display_name': 'Digital Transformation',
             'count': 25}]})


def _call(args, tmp_path, monkeypatch, handler=extra):
    monkeypatch.setenv('SCOPUS_MCP_OUTPUT_DIR', str(tmp_path))
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    seen = []

    def spy(request, path, p):
        seen.append(dict(p, path=path))
        return handler(request, path, p)
    with patch.object(server, 'client', MagicMock()), \
            patch.object(server, 'openalex', client(taxonomy_router(spy))):
        text = asyncio.new_event_loop().run_until_complete(
            server.handle_call_tool('topic_profile', args))[0].text
    return text, seen


def test_ranked_shares_and_unclassified():
    groups = {'17': {'name': 'CS', 'count': 30}, '14': {'name': 'Bus', 'count': 10},
              'unknown': {'name': 'unknown', 'count': 5}}
    r = ranked(groups, 50, 1)
    assert r['rows'] == [{'id': '17', 'name': 'CS', 'n': 30, 'share': 0.6}]
    assert (r['distinct'], r['classified'], r['unclassified'], r['not_listed']) == (2, 40, 5, 5)


def test_field_by_year_shares_use_the_years_total():
    per_year = {2019: {'17': {'name': 'CS', 'count': 8}},
                2020: {'17': {'name': 'CS', 'count': 3}, '14': {'name': 'Bus', 'count': 3}}}
    m = field_by_year(per_year, {2019: 10, 2020: 12})
    assert m['fields']['17']['shares'] == {2019: 0.8, 2020: 0.25}
    assert m['fields']['14']['counts'] == {2020: 3}


def test_first_year_over_threshold():
    assert first_year_over({2019: 0.01, 2020: 0.05, 2021: 0.2}, 0.05) == 2020
    assert first_year_over({2019: 0.01}, 0.05) is None
    assert first_year_over({2019: None, 2020: 0.1}, 0.05) == 2020


def test_profile_end_to_end(tmp_path, monkeypatch):
    text, seen = _call({'query': 'digital transformation', 'from_year': 2019, 'to_year': 2021},
                       tmp_path, monkeypatch)
    assert 'Topic profile of 46 papers' in text
    assert 'Record types: article, review, book, book-chapter.' in text
    # CS: 26 of 46 papers
    assert re.search(r'\b26\s+56\.5%\s+Computer Science', text)
    data = json.loads(next(tmp_path.glob('topic-profile-*.json')).read_text())
    fields = {f['name']: f for f in data['fields']['rows']}
    assert fields['Computer Science']['n'] == 26 and fields['Computer Science']['share'] == round(26 / 46, 4)
    assert data['fields']['unclassified'] == 3
    fy = data['field_by_year']['fields']
    # Business: 1/10, 5/15, 8/21 -> first reaches 5% in 2019 (10%).
    assert fy['14']['first_year_over_threshold'] == 2019
    # Social Sciences: absent 2019, 1/15 = 6.7% in 2020.
    assert fy['33']['first_year_over_threshold'] == 2020
    assert (tmp_path.glob('*field-by-year.csv'))
    # 1 year group + 3 level groups + 3 per-year = 7 requests, none per paper.
    groups = [s for s in seen if 'group_by' in s]
    assert len(groups) == 7 and data['requests'] == 7
    assert all('type:article|review|book|book-chapter' in s['filter'] for s in groups)


def test_profile_threshold_changes_first_year(tmp_path, monkeypatch):
    _call({'query': 'x', 'share_threshold': 0.5}, tmp_path, monkeypatch)
    data = json.loads(next(tmp_path.glob('topic-profile-*.json')).read_text())
    assert data['field_by_year']['fields']['14']['first_year_over_threshold'] is None
    assert data['field_by_year']['fields']['17']['first_year_over_threshold'] == 2019


def test_profile_work_types_all_removes_the_type_filter(tmp_path, monkeypatch):
    _, seen = _call({'query': 'x', 'work_types': 'all'}, tmp_path, monkeypatch)
    assert all('type:' not in s['filter'].replace('title_and_abstract', '')
               for s in seen if 'group_by' in s)


def test_profile_with_topics_adds_topic_filter_and_reports_scope(tmp_path, monkeypatch):
    text, seen = _call({'query': 'x', 'topics': ['T10346'], 'topic_match': 'any'},
                       tmp_path, monkeypatch)
    assert text.startswith('Topic scope:')
    assert all('topics.id:T10346' in s['filter'] for s in seen if 'group_by' in s)


def test_profile_with_no_matches_raises(tmp_path, monkeypatch):
    def none(request, path, p):
        if 'group_by' in p:
            return httpx.Response(200, json={'meta': {'count': 0}, 'group_by': []})
    text, _ = _call({'query': 'x'}, tmp_path, monkeypatch, handler=none)
    assert text.startswith('Error: No papers match')


def test_profile_refuses_scopus_source_and_long_spans(tmp_path, monkeypatch):
    text, _ = _call({'query': 'x', 'source': 'scopus'}, tmp_path, monkeypatch)
    assert 'OpenAlex only' in text

    def many(request, path, p):
        if p.get('group_by') == 'publication_year':
            return httpx.Response(200, json={'meta': {'count': 99}, 'group_by': [
                {'key': str(1950 + i), 'key_display_name': 'y', 'count': 1} for i in range(60)]})
    text, _ = _call({'query': 'x'}, tmp_path, monkeypatch, handler=many)
    assert 'pass from_year' in text
