"""Command-line entry point: start the server, store a secret, or explain what is missing."""
import sys

USAGE = """usage: scopus-plus-mcp [set-key NAME]

  (no arguments)   start the MCP server
  set-key NAME     save a secret in the OS secret store; you type it at a prompt.
                   NAME is one of: api_key, insttoken, openalex_api_key,
                   core_api_key, semantic_scholar_api_key.
                   Restart your MCP client afterwards."""


def _set_key(args) -> None:
    from .config import SECRET_ACCOUNTS, store_secret
    if len(args) != 1 or args[0] not in SECRET_ACCOUNTS:
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)
    try:
        store_secret(args[0])
    except RuntimeError as exc:
        print(f"scopus-plus-mcp: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Saved {args[0]}. Restart your MCP client to pick it up.")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in ('-h', '--help'):
        print(USAGE)
        return
    if args and args[0] == 'set-key':
        _set_key(args[1:])
        return
    try:
        # Importing the server builds the Scopus client, which needs the key.
        from .server import start
    except ValueError as exc:
        print(f"scopus-plus-mcp: {exc}", file=sys.stderr)
        print("See https://github.com/michalhron/scopus-plus-mcp#install",
              file=sys.stderr)
        raise SystemExit(1)
    start()
