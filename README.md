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

### Run the job

```bash
uv run python -u terraform_discovery.py
```

### Notes

- On first run, the cloud-platform-environments repository is cloned into TEMP_DIR.
- On subsequent runs, it pulls latest changes from origin.
