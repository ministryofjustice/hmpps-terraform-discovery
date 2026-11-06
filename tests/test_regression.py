"""Compare discovery output for real namespaces against tfparse snapshots.

Fixtures are copies of namespaces from ministryofjustice/cloud-platform-environments
and ``expected.json`` was generated with tfparse 0.6.22 - see fixtures/README.md.
"""

import json
from pathlib import Path

import pytest

from terraform_discovery import build_namespace_data
from tf_parser import load_from_path

FIXTURES = Path(__file__).parent / 'fixtures'
NAMESPACES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())


def normalise(data):
  for value in data.values():
    if isinstance(value, list):
      value.sort(key=lambda x: (x.get('tf_path') or '', str(x.get('tf_filename'))))
  return json.loads(json.dumps(data, sort_keys=True, default=str))


@pytest.mark.parametrize('namespace', NAMESPACES)
def test_namespace_matches_tfparse_snapshot(namespace):
  parsed = load_from_path(str(FIXTURES / namespace / 'resources'))
  actual = normalise(build_namespace_data(namespace, parsed))
  expected = normalise(json.loads((FIXTURES / namespace / 'expected.json').read_text()))
  assert actual == expected
