"""How the fields of a corpus connect: who cites whom across disciplines.

Works on records already fetched (each OpenAlex record carries the field and
subfield of its primary topic), so it adds no requests. Scopus records have no
such attributes and are left out; nothing is invented for them.
"""
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

UNCLASSIFIED = 'Unclassified'


def has_levels(nodes: Dict[str, Dict[str, Any]], level: str = 'field') -> bool:
    return any(n.get(level) for n in nodes.values())


def flow_matrix(nodes: Dict[str, Dict[str, Any]], edges: Iterable[Tuple[str, str]],
                level: str = 'field', top: int = 5) -> Dict[str, Any]:
    """Citations between the fields (or subfields) of citing and cited papers.

    edges are (citing id, cited id) pairs inside the corpus. Rows of the
    matrix are the citing field, columns the cited field. Edges with an
    unclassified end are counted apart and left out of the shares."""
    cells: Counter = Counter()
    unclassified = 0
    total = 0
    for citing, cited in edges:
        total += 1
        a, b = nodes.get(citing, {}).get(level), nodes.get(cited, {}).get(level)
        if not a or not b:
            unclassified += 1
            continue
        cells[(a, b)] += 1
    classified = total - unclassified
    within = sum(n for (a, b), n in cells.items() if a == b)
    cross = classified - within
    matrix: Dict[str, Dict[str, int]] = {}
    for (a, b), n in cells.items():
        matrix.setdefault(a, {})[b] = n
    out_of: Counter = Counter()
    for (a, _), n in cells.items():
        out_of[a] += n
    by_field = {a: {'citations_made': out_of[a],
                    'within_share': round(matrix[a].get(a, 0) / out_of[a], 3)}
                for a in matrix}
    flows = sorted(((a, b, n) for (a, b), n in cells.items() if a != b),
                   key=lambda t: (-t[2], t[0], t[1]))
    return {
        'level': level,
        'edges': total,
        'edges_classified': classified,
        'edges_unclassified': unclassified,
        'within': within,
        'cross': cross,
        'within_share': round(within / classified, 3) if classified else None,
        'cross_share': round(cross / classified, 3) if classified else None,
        'matrix_citing_by_cited': {a: dict(sorted(row.items(), key=lambda kv: -kv[1]))
                                   for a, row in sorted(matrix.items())},
        'by_field': by_field,
        'largest_cross_flows': [{'citing': a, 'cited': b, 'n': n,
                                 'share_of_cross': round(n / cross, 3) if cross else None}
                                for a, b, n in flows[:top]],
    }


def summary_lines(flow: Dict[str, Any]) -> List[str]:
    if not flow['edges_classified']:
        return [f"Field flows: no citation edge has a {flow['level']} at both ends."]
    lines = [
        f"Citations by {flow['level']} ({flow['edges_classified']} of {flow['edges']} edges "
        f"classified at both ends): {round(100 * flow['within_share'])}% within a {flow['level']}, "
        f"{round(100 * flow['cross_share'])}% across."
    ]
    if flow['largest_cross_flows']:
        lines.append("Largest cross-field flows (citing → cited): " + '; '.join(
            f"{f['citing']} → {f['cited']} {f['n']}" for f in flow['largest_cross_flows'][:3]))
    return lines


def field_mix(members: Iterable[str], nodes: Dict[str, Dict[str, Any]],
              level: str = 'field', top: int = 5) -> Optional[Dict[str, Any]]:
    """Fields of a group of papers: shares, the dominant one, and whether the
    group is one discipline or a meeting point of several."""
    counts = Counter(nodes[m].get(level) or UNCLASSIFIED for m in members if m in nodes)
    n = sum(counts.values())
    if not n or set(counts) == {UNCLASSIFIED}:
        return None
    classified = n - counts.get(UNCLASSIFIED, 0)
    ranked = [(f, c) for f, c in counts.most_common() if f != UNCLASSIFIED]
    dominant, dominant_n = ranked[0]
    return {
        'fields': [{'field': f, 'n': c, 'share': round(c / n, 3)} for f, c in counts.most_common(top)],
        'dominant': dominant,
        'dominant_share': round(dominant_n / classified, 3),
        # A front is a meeting point when no field holds a majority.
        'kind': 'single field' if dominant_n / classified >= 0.8 else
                'mixed' if dominant_n / classified > 0.5 else 'meeting point',
    }


def path_fields(path: List[str], nodes: Dict[str, Dict[str, Any]],
                level: str = 'field') -> Dict[str, Any]:
    """The field of each paper on a path, and where the path crosses fields."""
    seq = [{'id': k, 'year': nodes.get(k, {}).get('year'),
            level: nodes.get(k, {}).get(level) or UNCLASSIFIED} for k in path]
    crossings = [{'from': a['id'], 'to': b['id'], 'from_field': a[level], 'to_field': b[level]}
                 for a, b in zip(seq, seq[1:])
                 if a[level] != b[level] and UNCLASSIFIED not in (a[level], b[level])]
    route = []
    for s in seq:
        if not route or route[-1] != s[level]:
            route.append(s[level])
    return {'papers': seq, 'route': route, 'crossings': crossings}
