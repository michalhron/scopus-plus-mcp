"""Named journal baskets and ISSN scoping for searches and citation walks.

Journal names are unreliable filters in Scopus (the same journal appears as
"MIS Quarterly: Management Information Systems" and "MIS Quarterly
Management Information Systems"), so scoping always goes through ISSNs.
Print and electronic ISSNs are both listed: ISSN() matches either, and
OpenAlex source ISSNs include both.
"""
import re
from pathlib import Path
from typing import Dict, List, Optional, Union

from .openalex import chunk_or

# AIS Senior Scholars' Basket of Eight.
BASKET_OF_EIGHT = {
    'European Journal of Information Systems': ['0960-085X', '1476-9344'],
    'Information Systems Journal': ['1350-1917', '1365-2575'],
    'Information Systems Research': ['1047-7047', '1526-5536'],
    # Scopus files JAIS records under either number, inconsistently.
    'Journal of the Association for Information Systems': ['1536-9323', '1558-3457'],
    'Journal of Information Technology': ['0268-3962', '1466-4437'],
    'Journal of Management Information Systems': ['0742-1222', '1557-928X'],
    'Journal of Strategic Information Systems': ['0963-8687', '1873-1198'],
    'MIS Quarterly': ['0276-7783', '2162-9730'],
}

BASKETS: Dict[str, Dict[str, List[str]]] = {
    'basket_of_eight': BASKET_OF_EIGHT,
}
ALIASES = {
    'ais8': 'basket_of_eight',
    'basket_of_8': 'basket_of_eight',
    'senior_scholars_basket': 'basket_of_eight',
    'ais_basket': 'basket_of_eight',
}

SCOPE_SCHEMA = {
    "description": (
        "Restrict to journals, by ISSN: a list of ISSNs, a basket name "
        "('basket_of_eight' / 'ais8': the AIS Senior Scholars' Basket of "
        "Eight), or the path to a CSV file with an ISSN column (find_journals "
        "output, a Litbaskets export, a list you keep locally; columns named "
        "like issn or eissn are read, several ISSNs per cell allowed). ISSNs "
        "are used rather than journal names, which Scopus spells "
        "inconsistently. On OpenAlex, lists over 50 journals run as several "
        "requests."
    ),
    "anyOf": [
        {"type": "string"},
        {"type": "array", "items": {"type": "string"}},
    ],
}

_ISSN = re.compile(r'^(\d{4})-?(\d{3}[\dXx])$')


def normalize_issn(issn: str) -> str:
    m = _ISSN.match(str(issn).strip())
    if not m:
        raise ValueError(f"Not an ISSN: {issn!r} (expected NNNN-NNNN).")
    return f"{m.group(1)}-{m.group(2).upper()}"


CSV_SUFFIXES = ('.csv', '.tsv', '.txt')
MAX_SCOPE_FILE = 5_000_000


def issns_from_file(path: str) -> List[str]:
    """ISSNs from every column whose header contains 'issn' in a CSV or TSV
    file (find_journals gives issn and eissn; Litbaskets exports and local
    lists differ in spelling). Cells may hold several ISSNs."""
    import csv
    p = Path(path).expanduser()
    if p.suffix.lower() not in CSV_SUFFIXES:
        raise ValueError(f"A scope file must be a {'/'.join(CSV_SUFFIXES)} file, not {p.name!r}.")
    if not p.is_file():
        raise ValueError(f"Scope file {str(p)!r} does not exist.")
    if p.stat().st_size > MAX_SCOPE_FILE:
        raise ValueError(f"Scope file {p.name!r} is over {MAX_SCOPE_FILE // 1_000_000} MB.")
    text = p.read_text(encoding='utf-8-sig', errors='replace')
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    columns = [c for c in (reader.fieldnames or []) if c and 'issn' in c.lower()]
    if not columns:
        raise ValueError(f"{p.name!r} has no column with 'issn' in its header "
                         f"(found: {', '.join(reader.fieldnames or []) or 'none'}).")
    found: List[str] = []
    for row in reader:
        for col in columns:
            for token in re.split(r'[,;\s|]+', row.get(col) or ''):
                if not token:
                    continue
                try:
                    found.append(normalize_issn(token))
                except ValueError:
                    continue  # blanks and 'n/a' are common in exports
    if not found:
        raise ValueError(f"{p.name!r} has an ISSN column but no valid ISSNs in it.")
    return list(dict.fromkeys(found))


def resolve_scope(scope: Union[None, str, List[str]]) -> Optional[List[str]]:
    """ISSN list for a scope argument; None when unscoped.

    A string is a basket name, a CSV file path, or a comma-separated ISSN
    list."""
    if scope is None or scope == '' or scope == []:
        return None
    if isinstance(scope, str):
        key = scope.strip().lower().replace(' ', '_').replace('-', '_')
        key = ALIASES.get(key, key)
        if key in BASKETS:
            return [i for issns in BASKETS[key].values() for i in issns]
        if scope.strip().lower().endswith(CSV_SUFFIXES):
            return issns_from_file(scope.strip())
        scope = [s for s in re.split(r'[,\s;]+', scope) if s]
    try:
        issns = [normalize_issn(s) for s in scope]
    except ValueError as exc:
        raise ValueError(
            f"{exc} Known baskets: {', '.join(sorted(BASKETS) + sorted(ALIASES))}."
        ) from None
    return list(dict.fromkeys(issns))


def scopus_issn_clause(issns: List[str]) -> str:
    """'(ISSN(0276-7783) OR ISSN(1047-7047) ...)' for a Scopus query."""
    return '(' + ' OR '.join(f'ISSN({i.replace("-", "")})' for i in issns) + ')'


def scope_scopus_query(query: str, issns: Optional[List[str]]) -> str:
    if not issns:
        return query
    return f'({query}) AND {scopus_issn_clause(issns)}'


def openalex_issn_filter(issns: List[str]) -> Union[str, List[str]]:
    """OpenAlex filter fragment: primary_location.source.issn:a|b|...

    Long lists are split into chunks of OR_CHUNK ISSNs and come back as a list
    of alternatives; the OpenAlex client runs one request chain per chunk and
    merges the results."""
    return chunk_or('primary_location.source.issn', issns)
