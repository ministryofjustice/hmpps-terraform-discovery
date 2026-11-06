FROM ghcr.io/ministryofjustice/hmpps-python:python3.13-alpine AS base

# git is needed to clone cloud-platform-environments
USER root
RUN apk add --no-cache git

USER 2000
# initialise uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY ./terraform_discovery.py ./tf_parser.py ./

CMD [ "uv", "run", "--no-dev", "python", "-u", "terraform_discovery.py" ]
