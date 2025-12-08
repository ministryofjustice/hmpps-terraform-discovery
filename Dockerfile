FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app

RUN apt update && apt install -y git && rm -rf /var/lib/apt/lists/*

RUN addgroup --gid 2000 --system appgroup && \
    adduser --uid 2000 --system appuser --gid 2000 --home /home/appuser

# Ensure the workdir is owned by the unprivileged user before switching
RUN chown -R 2000:2000 /app

USER 2000
# initialise uv
COPY pyproject.toml .
RUN uv sync

# copy the dependencies from builder stage
COPY ./terraform_discovery.py .

CMD [ "uv", "run", "python", "-u", "terraform_discovery.py" ]
