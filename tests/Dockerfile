# Memosyne clean-install verification image (NOT the runtime image).
#
# Proves, in a fresh environment with no private data and no host caches,
# that the public source installs and runs end to end:
#
#   stage `core`    core install only  → unit tests, keyword-only quickstart, MCP smoke
#   stage `search`  + the search extra → dense quickstart, MCP smoke
#
# Each check is a RUN step, so a failed build IS the failure signal.
#
#   docker build -f tests/Dockerfile --target core   -t memosyne-verify-core .
#   docker build -f tests/Dockerfile --target search -t memosyne-verify .

FROM python:3.11-slim AS core

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=600 \
    MEMOSYNE_HF_OFFLINE=0

RUN apt-get update && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -e ".[dev]"
RUN python -m pytest tests/unit -q
RUN memosyne quickstart
RUN python tests/test_mcp_smoke.py
RUN memosyne health

FROM core AS search

RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu -e ".[search]"
RUN rm -rf sample_vault/_artifacts && memosyne quickstart
RUN python tests/test_mcp_smoke.py

CMD ["memosyne", "health"]
