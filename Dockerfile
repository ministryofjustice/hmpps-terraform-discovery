FROM python:3.10 AS builder
COPY requirements.txt .

RUN addgroup --gid 2000 --system appgroup && \
    adduser --uid 2000 --system appuser --gid 2000 --home /home/appuser

USER 2000

# install dependencies to the local user directory
RUN pip install --user -r requirements.txt

FROM python:3.10-slim
WORKDIR /app

RUN apt update && apt install -y git && rm -rf /var/lib/apt/lists/*

RUN addgroup --gid 2000 --system appgroup && \
    adduser --uid 2000 --system appuser --gid 2000 --home /home/appuser

# copy the dependencies from builder stage
COPY --chown=appuser:appgroup --from=builder /home/appuser/.local /home/appuser/.local
COPY ./terraform_discovery.py .
COPY ./classes ./classes

# update PATH environment variable
ENV PATH=/home/appuser/.local:$PATH

USER 2000

CMD [ "python", "-u", "terraform_discovery.py" ]
