FROM ghcr.io/astral-sh/uv:python3.13-slim
WORKDIR /app

RUN addgroup -g 2000 appgroup && \
    adduser -u 2000 -G appgroup -h /home/appuser -D appuser

# initialise uv
COPY pyproject.toml .
RUN uv sync

USER 2000

# copy the dependencies from builder stage
COPY ./terraform_discovery.py .

CMD [ "uv", "run", "python", "-u", "terraform_discovery.py" ]
