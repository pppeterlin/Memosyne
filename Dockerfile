# Memosyne runtime image.
#
#   docker build -t memosyne .                                # core + dense search
#   docker build --build-arg MEMOSYNE_EXTRAS=dev -t memosyne . # core only (keyword search)
#
# Your data stays outside the image: mount a directory at /data. It will hold
# vault/ (your memories), spring/ (the drop zone) and optionally .env.

FROM python:3.11-slim

ARG MEMOSYNE_EXTRAS=search

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MEMOSYNE_HOME=/data \
    MEMOSYNE_VAULT_DIR=/data/vault \
    MEMOSYNE_ARTIFACT_DIR=/data/vault/_artifacts \
    MEMOSYNE_SPRING_DIR=/data/spring \
    MEMOSYNE_HF_OFFLINE=0

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# The extra index serves CPU-only torch wheels (far smaller than the CUDA build).
RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        ".[${MEMOSYNE_EXTRAS}]"

VOLUME /data
ENTRYPOINT ["memosyne"]
CMD ["health"]
