"""Arithmetic for the topic profile: shares by field, subfield and topic, and
when each field joined the debate."""
from typing import Any, Dict, List, Optional

UNKNOWN = 'unknown'


def ranked(groups: Dict[str, Dict[str, Any]], total: int, top: int) -> Dict[str, Any]:
    """Top groups by count with their share of all `total` works. Works
    without the attribute are reported apart as 'unclassified'."""
    unclassified = (groups.get(UNKNOWN) or {}).get('count', 0)
    rows = [{'id': gid, 'name': g['name'], 'n': g['count'],
             'share': round(g['count'] / total, 4) if total else None}
            for gid, g in groups.items() if gid != UNKNOWN]
    rows.sort(key=lambda r: (-r['n'], r['name'] or ''))
    # Works beyond the groups OpenAlex returned (a field has no missing ones,
    # topics can exceed the page) also count as unclassified only if they
    # lack the attribute; the remainder is shown, not guessed.
    classified = sum(r['n'] for r in rows)
    return {'rows': rows[:top], 'distinct': len(rows), 'classified': classified,
            'unclassified': unclassified, 'not_listed': max(0, total - classified - unclassified)}


def field_by_year(per_year: Dict[int, Dict[str, Dict[str, Any]]],
                  year_totals: Dict[int, int]) -> Dict[str, Any]:
    """{'years': [...], 'fields': {id: {'name', 'counts': {year: n},
    'shares': {year: share of that year's papers}}}}."""
    years = sorted(year_totals)
    fields: Dict[str, Dict[str, Any]] = {}
    for year in years:
        for fid, g in per_year.get(year, {}).items():
            if fid == UNKNOWN:
                continue
            f = fields.setdefault(fid, {'name': g['name'], 'counts': {}, 'shares': {}})
            f['counts'][year] = g['count']
            f['shares'][year] = round(g['count'] / year_totals[year], 4) if year_totals[year] else None
    return {'years': years, 'fields': fields}


def first_year_over(shares: Dict[int, Optional[float]], threshold: float) -> Optional[int]:
    """First year the field's share of that year's papers reached `threshold`."""
    for year in sorted(shares):
        if shares[year] is not None and shares[year] >= threshold:
            return year
    return None
