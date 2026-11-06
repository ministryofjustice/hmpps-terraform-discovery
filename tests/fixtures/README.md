# Regression fixtures

Each directory is a copy of `namespaces/live.cloud-platform.service.justice.gov.uk/<namespace>/resources/*.tf`
from [cloud-platform-environments](https://github.com/ministryofjustice/cloud-platform-environments)
(commit `97e8e7a`). They were chosen to cover RDS, ElastiCache, egress controls,
the HMPPS template, Pingdom checks, `count`/`for_each`, cross-module references,
a trailing comment without a final newline and a namespace with no modules.

`expected.json` is the output of `terraform_discovery.build_namespace_data` when
the namespace is parsed with [tfparse](https://github.com/cloud-custodian/tfparse)
0.6.22, the library `tf_parser` replaces. tfparse does not run on Alpine, but it
does run on macOS and glibc Linux, so to regenerate a snapshot:

```sh
uv run --with tfparse==0.6.22 python - <<'PY'
import json
from tfparse import load_from_path
from terraform_discovery import build_namespace_data

ns = 'hmpps-example-dev'
data = build_namespace_data(ns, load_from_path(f'tests/fixtures/{ns}/resources'))
with open(f'tests/fixtures/{ns}/expected.json', 'w') as f:
  json.dump(data, f, indent=2, sort_keys=True, default=str)
  f.write('\n')
PY
```
