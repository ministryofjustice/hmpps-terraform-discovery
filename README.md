# hmpps-terraform-discovery
Service that queries cloudplatform terraform projects and collects information about hmpps projects and pushes it to the service catalogue.

## Run locally

### Prerequisites

- Python 3.13+
- uv installed: https://docs.astral.sh/uv/getting-started/installation/
- Git (required because the job clones cloud-platform-environments)

### Required environment variables

Set these before running:

- SERVICE_CATALOGUE_API_ENDPOINT
- SERVICE_CATALOGUE_API_KEY
- SLACK_BOT_TOKEN

Optional:

- LOG_LEVEL (default: INFO)
- TEMP_DIR (default: /tmp/cp_envs)
- SLACK_NOTIFY_CHANNEL
- SLACK_ALERT_CHANNEL

### Install dependencies

Create a local virtual environment (optional but recommended):

```bash
uv venv
source .venv/bin/activate
```

Then install dependencies:

```bash
uv sync
```

## Local run job

```bash
uv run python -u terraform_discovery.py
```

### Notes

- On first run, the cloud-platform-environments repository is cloned into TEMP_DIR.
- On subsequent runs, it pulls latest changes from origin.

## Terraform parsing

Terraform files are parsed by `tf_parser.py`, a small pure-Python replacement for
[tfparse](https://github.com/cloud-custodian/tfparse). tfparse wraps a Go
c-shared library that can't be loaded on musl (see
[golang/go#54805](https://github.com/golang/go/issues/54805)), which blocked us
from using the shared `hmpps-python` Alpine image.

`tf_parser` uses [python-hcl2](https://github.com/amplify-education/python-hcl2)
to parse HCL and evaluates the subset of Terraform that discovery needs: variable
defaults, locals, references to resources and module arguments, string templates,
operators, conditionals, common built-in functions and `count`/`for_each`. Output
follows tfparse's format, so the `extract_*` functions are unchanged. Anything it
can't resolve (e.g. remote state or unsupported functions) comes back as the
original expression text or an `__attribute__` reference rather than an error.

## Tests

```bash
uv run pytest
```

- `tests/test_tf_parser.py` - unit tests for the parser
- `tests/test_regression.py` - compares discovery output for real namespaces
  against snapshots generated with tfparse (see `tests/fixtures/README.md`)
