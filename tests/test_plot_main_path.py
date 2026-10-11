"""plot_main_path.py: a paper without a year must not stretch the axis to 0,
and --lanes-by-field draws one lane per field on the path."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = (Path(__file__).parent.parent / 'plugins' / 'scopus-plus-mcp' / 'skills'
          / 'main-path' / 'scripts' / 'plot_main_path.py')


def _run(tmp_path, *extra):
    nodes = [
        {'id': 'A', 'year': 2015, 'field': 'Business', 'subfield': 'Strategy'},
        {'id': 'B', 'year': 2018, 'field': 'Computer Science', 'subfield': 'Information Systems'},
        {'id': 'C', 'year': 2021, 'field': 'Business', 'subfield': 'Strategy'},
        {'id': 'D', 'year': None, 'field': None, 'subfield': None},  # no year
    ]
    (tmp_path / 'c.json').write_text(json.dumps({'nodes': nodes}))
    (tmp_path / 'r.json').write_text(json.dumps({
        'main_path': ['A', 'B', 'C'], 'main_path_labels': ['A 2015', 'B 2018', 'C 2021']}))
    out = tmp_path / 'm.png'
    return subprocess.run(
        [sys.executable, '-I', str(SCRIPT), '--corpus', str(tmp_path / 'c.json'),
         '--result', str(tmp_path / 'r.json'), '--out', str(out), *extra],
        capture_output=True, text=True, timeout=120), out


def test_paper_without_a_year_does_not_break_the_axis(tmp_path):
    result, out = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert out.exists() and out.stat().st_size > 0


def test_lanes_by_field_and_subfield_run(tmp_path):
    for level in ('field', 'subfield'):
        result, out = _run(tmp_path, '--lanes-by-field', level)
        assert result.returncode == 0, result.stderr
        assert out.exists()
