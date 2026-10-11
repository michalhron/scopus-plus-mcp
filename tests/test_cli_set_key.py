import sys

import pytest

from scopus_mcp import cli, config


def test_set_key_rejects_unknown_name(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['scopus-plus-mcp', 'set-key', 'nonsense'])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert 'openalex_api_key' in capsys.readouterr().err


def test_set_key_calls_store_secret_without_a_value_on_argv(monkeypatch, capsys):
    saved = []
    monkeypatch.setattr(config, 'store_secret', saved.append)
    monkeypatch.setattr(sys, 'argv', ['scopus-plus-mcp', 'set-key', 'openalex_api_key'])
    cli.main()
    assert saved == ['openalex_api_key']
    assert 'Saved openalex_api_key' in capsys.readouterr().out


def test_store_secret_refuses_unknown_account():
    with pytest.raises(RuntimeError):
        config.store_secret('whatever')


def test_macos_store_uses_prompting_security_call(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(config.subprocess, 'run',
                        lambda cmd, **kw: calls.append(cmd) or type('R', (), {'returncode': 0})())
    config.store_secret('openalex_api_key')
    cmd = calls[0]
    assert cmd[-1] == '-w' and 'openalex_api_key' in cmd  # no value on the command line
