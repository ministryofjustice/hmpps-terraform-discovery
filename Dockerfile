FROM ghcr.io/ministryofjustice/hmpps-python:python3.13-alpine-20251203
WORKDIR /app

USER 0

# Install build dependencies:
# - go: to compile the terraform core inside tfparse
# - gcc, musl-dev, libffi-dev: for python c-extensions
# - git: to clone the repo
RUN apk add --no-cache go git gcc musl-dev libffi-dev wget unzip && \
    wget https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_arm64.zip && \
    unzip terraform_1.9.8_linux_arm64.zip && \
    mv terraform /usr/local/bin/ && \
    rm terraform_1.9.8_linux_arm64.zip && \
    apk del wget unzip


# Ensure the workdir is owned by the unprivileged user before switching
RUN chown -R 2000:2000 /app
USER 2000

# initialise uv
COPY pyproject.toml .
RUN uv sync 

# copy the dependencies from builder stage
COPY ./terraform_discovery.py .

CMD [ "uv", "run", "python", "-u", "terraform_discovery.py" ]
