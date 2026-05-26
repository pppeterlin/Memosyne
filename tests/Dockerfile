# ── Memosyne clean-install verification image ──
#
# This is NOT the runtime image. It exists so that the v0.4 OSS-release
# exit criterion "clean install works" can be verified reproducibly: a
# fresh Python environment with only the public surface, no private
# vault, no host caches, no host-installed model weights.
#
# A successful build means:
#   * Public source compiles
#   * pip install -e . + requirements.txt resolves and installs
#   * `memosyne rebuild` indexes sample_vault end to end
#   * `memosyne eval --sample` passes
#   * tests/test_mcp_smoke.py passes
#
# Build:
#   docker build -f tests/Dockerfile -t memosyne-verify .
#
# Run (optional — image already proved healthy at build time):
#   docker run --rm memosyne-verify python memosyne.py health
#
# The verification commands run as RUN steps below, so a failed build
# IS the failure signal. No CI harness required.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=600 \
    PIP_RETRIES=8 \
    MEMOSYNE_HF_OFFLINE=0

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libsnappy-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch first so we don't pull the multi-GB CUDA build.
RUN pip install --no-cache-dir \
        torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu

# Application source. .dockerignore keeps the private submodule out.
COPY . /app

RUN pip install --no-cache-dir -e .
RUN pip install --no-cache-dir -r Personal_Brain_DB/00_System/requirements.txt

# Pre-warm the embedding model so later steps can stay offline-friendly.
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

ENV MEMOSYNE_VAULT_DIR=/app/sample_vault \
    MEMOSYNE_ARTIFACT_DIR=/app/sample_vault/_artifacts

# ── Verification steps ────────────────────────────────────────
# Each step is a separate RUN so a failure points at the exact stage.

RUN python memosyne.py rebuild

# Health is informational here: chronicle/tapestry/cache artifacts only
# materialize after the corresponding workflows run, which the verify
# image does not exercise. Failure is allowed; the build fails only if
# rebuild, eval, or the MCP smoke test below regress.
RUN python memosyne.py health || true

RUN python memosyne.py eval --sample

RUN python tests/test_mcp_smoke.py

CMD ["python", "memosyne.py", "health"]
