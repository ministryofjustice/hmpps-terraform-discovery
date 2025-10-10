FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app

RUN addgroup --gid 2000 --system appgroup && \
    adduser --uid 2000 --system appuser --gid 2000 --home /home/appuser

USER 2000

# initialise uv
COPY pyproject.toml .
RUN uv sync

USER 2000

# copy the dependencies from builder stage
COPY ./terraform_discovery.py .

CMD [ "uv", "run", "python", "-u", "terraform_discovery.py" ]
