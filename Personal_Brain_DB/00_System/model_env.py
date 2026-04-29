"""
Shared model runtime settings for Memosyne.

The retrieval stack is local-first: once embedding models are cached, search
should not block on network metadata checks. Set MEMOSYNE_HF_OFFLINE=0 when
bootstrapping a fresh machine that still needs to download the model.
"""

from __future__ import annotations

import os
import warnings


def configure_hf_runtime() -> bool:
    """Configure Hugging Face/SentenceTransformer runtime for local-first use."""
    offline = os.environ.get("MEMOSYNE_HF_OFFLINE", "1").strip().lower()
    enabled = offline not in {"0", "false", "no", "off"}

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    warnings.filterwarnings(
        "ignore",
        message="`resume_download` is deprecated.*",
        category=FutureWarning,
    )

    if enabled:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    return enabled
