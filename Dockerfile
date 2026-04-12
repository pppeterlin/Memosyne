# ── Memosyne Dockerfile ──
# Multi-stage build: keeps final image lean

FROM python:3.11-slim AS base

# System dependencies for python-snappy, kuzu, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libsnappy-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Stage 1: Install Python dependencies ──
FROM base AS deps

COPY Personal_Brain_DB/00_System/requirements.txt /tmp/requirements.txt

# Install CPU-only PyTorch first (much smaller than default CUDA build),
# then the rest of the dependencies.
RUN pip install --no-cache-dir \
        torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir \
        -r /tmp/requirements.txt

# ── Stage 2: Final image ──
FROM base AS final

# Copy installed packages from deps stage
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application code
COPY Personal_Brain_DB/00_System/ /app/Personal_Brain_DB/00_System/

# Create mount-point directories
RUN mkdir -p \
    /app/spring \
    /app/Personal_Brain_DB/10_Profile \
    /app/Personal_Brain_DB/20_AI_Chats \
    /app/Personal_Brain_DB/30_Journal \
    /app/Personal_Brain_DB/40_Projects \
    /app/Personal_Brain_DB/50_Knowledge \
    /app/Personal_Brain_DB/00_System/chroma_db \
    /app/Personal_Brain_DB/00_System/tapestry_db \
    /app/Personal_Brain_DB/00_System/flashrank_cache

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Default: run the chat interface
CMD ["python3", "Personal_Brain_DB/00_System/chat.py"]
