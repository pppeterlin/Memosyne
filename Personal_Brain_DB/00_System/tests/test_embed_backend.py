"""Unit tests for the pluggable embedding backend (embed_backend.py, v0.8).

涵蓋出口標準要求的四個面向:
  1. config 解析 + 向後相容（不設 env → local）
  2. batch（多段包一次請求 + 依 index 排序）
  3. dim mismatch 偵測（顯式宣告維度防 silent 污染）
  4. fail-fast（endpoint 不通 → raise,不靜默降級）

不需要真的網路 / GPU server — 用 fake HTTP 攔截。

執行:
    python3 -m unittest tests.test_embed_backend -v
"""

from __future__ import annotations

import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import embed_backend as eb  # noqa: E402


class TestResolveConfig(unittest.TestCase):

    def test_default_is_local_backward_compat(self):
        cfg = eb.resolve_config(env={})
        self.assertEqual(cfg.provider, "local")
        self.assertFalse(cfg.is_remote)
        self.assertEqual(cfg.model, eb.LOCAL_DEFAULT_MODEL)

    def test_invalid_provider_raises(self):
        with self.assertRaises(eb.EmbeddingBackendError):
            eb.resolve_config(env={"MEMOSYNE_EMBED_PROVIDER": "bogus"})

    def test_remote_requires_url(self):
        with self.assertRaises(eb.EmbeddingBackendError):
            eb.resolve_config(env={
                "MEMOSYNE_EMBED_PROVIDER": "ollama",
                "MEMOSYNE_EMBED_MODEL": "nomic-embed-text",
            })

    def test_ollama_config_parsed(self):
        cfg = eb.resolve_config(env={
            "MEMOSYNE_EMBED_PROVIDER": "ollama",
            "MEMOSYNE_EMBED_URL": "http://gpu-host:11434/",
            "MEMOSYNE_EMBED_MODEL": "nomic-embed-text",
            "MEMOSYNE_EMBED_DIM": "768",
        })
        self.assertEqual(cfg.provider, "ollama")
        self.assertEqual(cfg.url, "http://gpu-host:11434")  # trailing / 去掉
        self.assertEqual(cfg.dim, 768)
        self.assertTrue(cfg.is_remote)


class TestBatchAndProtocols(unittest.TestCase):
    """用 monkeypatch 攔截 _post_json,不打真網路。"""

    def setUp(self):
        self._orig = eb._post_json
        self.calls: list[dict] = []

    def tearDown(self):
        eb._post_json = self._orig

    def _patch(self, fake):
        eb._post_json = fake

    def test_ollama_batch_single_request(self):
        def fake(url, payload, headers, timeout):
            self.calls.append(payload)
            n = len(payload["input"])
            return {"embeddings": [[0.1, 0.2, 0.3] for _ in range(n)]}
        self._patch(fake)

        cfg = eb.EmbedConfig(provider="ollama", url="http://x:11434",
                             model="nomic-embed-text", batch_size=64)
        fn = eb.RemoteEmbeddingFunction(cfg)
        out = fn(["a", "b", "c"])

        self.assertEqual(len(out), 3)
        self.assertEqual(len(out[0]), 3)
        # 3 段在 batch_size=64 下應只發一次 HTTP
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["input"], ["a", "b", "c"])

    def test_batch_size_splits_requests(self):
        def fake(url, payload, headers, timeout):
            self.calls.append(payload)
            n = len(payload["input"])
            return {"embeddings": [[1.0, 2.0] for _ in range(n)]}
        self._patch(fake)

        cfg = eb.EmbedConfig(provider="ollama", url="http://x:11434",
                             model="m", batch_size=2)
        fn = eb.RemoteEmbeddingFunction(cfg)
        out = fn(["a", "b", "c", "d", "e"])

        self.assertEqual(len(out), 5)
        self.assertEqual(len(self.calls), 3)  # 2 + 2 + 1

    def test_openai_compat_reorders_by_index(self):
        def fake(url, payload, headers, timeout):
            self.calls.append((url, headers, payload))
            # 故意亂序回傳
            return {"data": [
                {"embedding": [9.0], "index": 1},
                {"embedding": [8.0], "index": 0},
            ]}
        self._patch(fake)

        cfg = eb.EmbedConfig(provider="openai-compat", url="http://api:8080",
                             model="text-embed", api_key="sk-test")
        fn = eb.RemoteEmbeddingFunction(cfg)
        out = fn(["first", "second"])

        self.assertEqual(out, [[8.0], [9.0]])  # 已依 index 排回
        url, headers, _ = self.calls[0]
        self.assertTrue(url.endswith("/v1/embeddings"))
        self.assertEqual(headers["Authorization"], "Bearer sk-test")


class TestDimMismatch(unittest.TestCase):

    def setUp(self):
        self._orig = eb._post_json

    def tearDown(self):
        eb._post_json = self._orig

    def test_dim_mismatch_raises(self):
        def fake(url, payload, headers, timeout):
            n = len(payload["input"])
            return {"embeddings": [[0.1, 0.2] for _ in range(n)]}  # dim=2
        eb._post_json = fake

        cfg = eb.EmbedConfig(provider="ollama", url="http://x:11434",
                             model="m", dim=768)  # 宣告 768,實得 2
        fn = eb.RemoteEmbeddingFunction(cfg)
        with self.assertRaises(eb.EmbeddingBackendError) as ctx:
            fn(["hello"])
        self.assertIn("維度不符", str(ctx.exception))


class TestFailFast(unittest.TestCase):

    def setUp(self):
        self._orig = eb._post_json

    def tearDown(self):
        eb._post_json = self._orig

    def test_connection_error_fails_fast_after_retries(self):
        attempts = {"n": 0}

        def fake(url, payload, headers, timeout):
            attempts["n"] += 1
            raise urllib.error.URLError("connection refused")
        eb._post_json = fake

        cfg = eb.EmbedConfig(provider="ollama", url="http://dead:11434",
                             model="m", retries=3)
        fn = eb.RemoteEmbeddingFunction(cfg)
        with self.assertRaises(eb.EmbeddingBackendError) as ctx:
            fn(["x"])
        # 重試 3 次後 fail-fast,不降級
        self.assertEqual(attempts["n"], 3)
        self.assertIn("不通", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
