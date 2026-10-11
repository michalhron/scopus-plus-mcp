import asyncio
from unittest.mock import patch

import pytest

from scopus_mcp.client import ScopusClient


def test_client_builds_without_a_scopus_key_and_scopus_calls_say_why():
    with patch('scopus_mcp.client.get_api_key',
               side_effect=ValueError('Scopus API Key not found.')):
        client = ScopusClient()
    assert client.api_key is None
    assert 'X-ELS-APIKey' not in client.headers
    with pytest.raises(ValueError, match="source='openalex'"):
        asyncio.run(client.search_scopus('x', count=1))
    report = asyncio.run(client.diagnose_connection())
    assert report['config']['api_key_present'] is False
