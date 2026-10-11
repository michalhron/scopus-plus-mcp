"""Shared mock OpenAlex transport for topic tests. Fixtures under
tests/fixtures/openalex_topics are hand-built to the documented API shape,
not recorded live."""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx

from scopus_mcp import openalex as oa
from scopus_mcp.openalex import OpenAlexClient

FIXTURES = Path(__file__).parent / 'fixtures' / 'openalex_topics'


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def params(request):
    return {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}


def client(handler):
    cache = MagicMock()
    cache.get.return_value = None
    with patch.dict(os.environ, {}):
        c = OpenAlexClient(cache=cache)
    c.client = httpx.AsyncClient(base_url=oa.BASE_URL, headers=dict(c.client.headers),
                                 transport=httpx.MockTransport(handler))
    return c


def taxonomy_router(extra=None):
    """Handler serving the taxonomy fixtures; `extra(request, path, p)` may
    answer first (return an httpx.Response) and sees every request."""
    seen = []

    def handler(request):
        path = request.url.path.strip('/')
        p = params(request)
        seen.append((path, p))
        if extra:
            r = extra(request, path, p)
            if r is not None:
                return r
        if path == 'fields':
            return httpx.Response(200, json=fixture('fields.json'))
        if path == 'subfields':
            return httpx.Response(200, json=fixture('subfields.json'))
        if path == 'topics':
            term = p.get('filter', '').lower()
            name = 'topics_marketing.json' if 'marketing' in term else 'topics_digital.json'
            return httpx.Response(200, json=fixture(name))
        if path == 'topics/T10346':
            return httpx.Response(200, json=fixture('topic_T10346.json'))
        if path.startswith('topics/'):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={'meta': {'count': 0, 'next_cursor': None}, 'results': [],
                                         'group_by': []})
    handler.seen = seen
    return handler
